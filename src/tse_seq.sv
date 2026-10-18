`default_nettype none
// One lane sequencer. 16-bit instructions, [15:13] op, [12:11] side-set;
// encodings, field positions and enumerations come from tse_isa_pkg, so this
// file holds no instruction numbers of its own.
//   WAIT ends on its own condition, on the lane's alternate condition
//   alt_cfg {idx[5:3], src[2:1], pol[0]} when alt is set, or on the timeout
//   (T cycles); priority own (both flags 0), alternate (AFLAG), timeout (TFLAG).
//   Both flags clear at dispatch. PIN indices 4 to 7 mean "pin i changed since
//   the WAIT dispatched" (compared with the level of its dispatch cycle).
//   A WAIT that ends on its own condition WAIT 1, EV, 0 acknowledges the timer.
//   CSRW to a configuration word (class L) lasts 3 cycles, the bank's write period.
//   C counts cycles from RUN (saturating at 0xFFFF); TMR cclr makes it read 0
//   in the next cycle, TMR gwe selects the guard threshold GT[gsel]; GUARD is
//   C >= GT[gsel] (JMP GUARD, FLAG index 0, MOV source C).
//   SIG writes the chip flags in its cycle; EMIT hands (code, X[7:0]) to the
//   event unit one cycle later and never stalls.
//   LDW and STW retire at dispatch and request the data window until the
//   lane's data slot s grants it; the next instruction waits until s + 1 (STW)
//   or s + 2 (LDW, OSR loaded from dmem_rdata in s + 1).
//   SET PINS and SET PINDIRS write imm[3:0] under the mask imm[7:4].
//   A word tse_legal rejects halts the lane at its dispatch: no effect, no
//   further fetch, halted set and interrupt bit 3 pulsed.
// Each instruction takes 1 + delay cycles when its word is buffered on time.
// Four virtual pins; side-set drives ss_base and ss_base + 1 modulo 4.
// Fetch: 32-bit words (two instructions) from a synchronous memory, data one
// cycle after a granted request, used straight from the memory output and kept
// in a two-entry buffer. A miss requests the word at once (granted in the
// lane's slot); the fill replaces the entry that does not hold the current
// instruction (least recently used). The successor's word is requested from
// the dispatch cycle on, also while a WAIT, PUSH or PULL is still waiting.
// TX mailbox: with LATCH = 1 (the chip) a latch word whose enable is one
// flop after h_tx_we; the host data register holds h_tx_data for 16 or more
// cycles, so the word shows the new value from the next cycle, as a flop would.
module tse_seq #(
    parameter bit LATCH = 0
) (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        run,
    input  logic [7:0]  start_pc,           // pc while stopped, so the first dispatch is there
    output logic        imem_req,
    output logic [6:0]  imem_addr,          // word address in the lane's window
    input  logic        imem_gnt,
    input  logic [31:0] imem_rdata,
    output logic        dmem_req,           // data window access, held until granted
    output logic        dmem_we,
    output logic [6:0]  dmem_addr,
    output logic [31:0] dmem_wdata,
    input  logic        dmem_gnt,
    input  logic [31:0] dmem_rdata,         // the LDW word, the cycle after dmem_gnt
    input  logic [5:0]  alt_cfg,
    input  logic [7:0]  flg,               // FLAG sources (event map)
    input  logic [3:0]  pin_in,
    output logic [3:0]  pin_out,
    output logic [3:0]  pin_oe,
    input  logic        side_en,
    input  logic [1:0]  ss_base,
    input  logic [1:0]  jmp_pin,
    input  logic [7:0]  ev,
    input  logic [5:0]  chip_flags,         // CF0..CF3, TRIG, TICK
    input  logic [63:0] gt,                 // guard thresholds {GT3, GT2, GT1, GT0}
    output logic [15:0] clock,              // C
    output logic [7:0]  pc_o,
    output logic        csr_we,
    output logic        csr_re,
    output logic [7:0]  csr_addr,
    output logic [31:0] csr_wdata,
    input  logic [31:0] csr_rdata,
    output logic        cmd_we,
    output logic [7:0]  cmd,
    input  logic        h_tx_we,
    input  logic [31:0] h_tx_data,
    output logic        h_tx_full,
    input  logic        h_rx_re,
    output logic [31:0] h_rx_data,
    output logic        h_rx_full,
    output logic [3:0]  irq,
    output logic        sig_we,             // chip flags: CF = (CF & ~mask) | (val & mask)
    output logic [3:0]  sig_mask,
    output logic [3:0]  sig_val,
    output logic        emit_v,             // event (code, X[7:0]) of the previous cycle
    output logic [5:0]  emit_code,
    output logic [7:0]  emit_data,
    output logic        timer_ack,
    output logic        halted,
    output logic        aflag_o,
    output logic        tflag_o
);
    logic [7:0]  pc;
    logic [15:0] x, y, t, tc;
    logic [31:0] osr, isr;
    logic [8:0]  dly;
    logic        held, tflag, aflag;
    logic [15:0] c_q;
    logic [3:0]  pin0, pchg;
    logic [1:0]  gsel;
    logic        guard;
    logic [31:0] h_tx_data_q;

    // two-entry word buffer, plus the word arriving from the memory this cycle
    logic [6:0]  e_tag [2];
    logic [31:0] e_word [2];
    logic [1:0]  e_val;
    logic        fresh;
    logic [6:0]  f_tag;

    logic        h0, h1, hf, first;
    logic [31:0] word;
    logic [15:0] instr;
    logic [4:0]  ic;
    logic        legal;
    logic        avail, dpend, dld, dwe_q, dacc;
    logic [6:0]  daddr_q;
    assign h0    = e_val[0] && e_tag[0] == pc[7:1];
    assign h1    = e_val[1] && e_tag[1] == pc[7:1];
    assign hf    = fresh && f_tag == pc[7:1];
    assign word  = hf ? imem_rdata : h0 ? e_word[0] : e_word[1];
    assign instr = pc[0] ? word[31:16] : word[15:0];
    assign ic    = tse_isa_pkg::tse_decode(instr);
    assign legal = tse_isa_pkg::tse_legal(instr);
    assign first = !held;
    assign avail = run && !halted && (hf || h0 || h1) && dly == 9'd0 && !dpend && !dld;

    // instruction fields
    logic [2:0] f_cond, f_dst, f_isrc, f_msrc, f_idx, f_dly3, f_wdly;
    logic [1:0] f_src, ss;
    logic [7:0] f_target, f_imm, f_csr;
    logic [4:0] f_mdly, f_n1;
    logic [8:0] f_d9;
    logic [4:0] f_tdly;
    logic [3:0] f_smask, f_sval;
    logic [1:0] f_gsel;
    logic       f_cclr, f_gwe;
    logic [5:0] f_ecode;
    logic [2:0] f_edly;
    logic       f_pol, f_to, f_alt, f_blk, f_ind;
    logic [6:0] f_a7;
    assign ss       = instr[tse_isa_pkg::SS_HI:tse_isa_pkg::SS_LO];
    assign f_cond   = instr[tse_isa_pkg::JMP_COND_HI:tse_isa_pkg::JMP_COND_LO];
    assign f_target = instr[tse_isa_pkg::JMP_TARGET_HI:tse_isa_pkg::JMP_TARGET_LO];
    assign f_pol    = instr[tse_isa_pkg::WAIT_POL_HI];
    assign f_src    = instr[tse_isa_pkg::WAIT_SRC_HI:tse_isa_pkg::WAIT_SRC_LO];
    assign f_idx    = instr[tse_isa_pkg::WAIT_IDX_HI:tse_isa_pkg::WAIT_IDX_LO];
    assign f_to     = instr[tse_isa_pkg::WAIT_TO_HI];
    assign f_alt    = instr[tse_isa_pkg::WAIT_ALT_HI];
    assign f_wdly   = instr[tse_isa_pkg::WAIT_DELAY_HI:tse_isa_pkg::WAIT_DELAY_LO];
    assign f_dst    = instr[tse_isa_pkg::SET_DST_HI:tse_isa_pkg::SET_DST_LO];
    assign f_imm    = instr[tse_isa_pkg::SET_IMM_HI:tse_isa_pkg::SET_IMM_LO];
    assign f_msrc   = instr[tse_isa_pkg::MOV_SRC_HI:tse_isa_pkg::MOV_SRC_LO];
    assign f_mdly   = instr[tse_isa_pkg::MOV_DELAY_HI:tse_isa_pkg::MOV_DELAY_LO];
    assign f_isrc   = instr[tse_isa_pkg::IN_SRC_HI:tse_isa_pkg::IN_SRC_LO];
    assign f_n1     = instr[tse_isa_pkg::OUT_N_HI:tse_isa_pkg::OUT_N_LO];
    assign f_dly3   = instr[tse_isa_pkg::OUT_DELAY_HI:tse_isa_pkg::OUT_DELAY_LO];
    assign f_blk    = instr[tse_isa_pkg::PUSH_BLK_HI];
    assign f_csr    = instr[tse_isa_pkg::CSRW_ADDR_HI:tse_isa_pkg::CSRW_ADDR_LO];
    assign f_ind    = instr[tse_isa_pkg::LDW_IND_HI];
    assign f_a7     = instr[tse_isa_pkg::LDW_ADDR_HI:tse_isa_pkg::LDW_ADDR_LO];
    assign f_d9     = instr[tse_isa_pkg::DLY_D_HI:tse_isa_pkg::DLY_D_LO];
    assign f_tdly   = instr[tse_isa_pkg::TMR_DELAY_HI:tse_isa_pkg::TMR_DELAY_LO];
    assign f_cclr   = instr[tse_isa_pkg::TMR_CCLR_HI];
    assign f_gwe    = instr[tse_isa_pkg::TMR_GWE_HI];
    assign f_gsel   = instr[tse_isa_pkg::TMR_GSEL_HI:tse_isa_pkg::TMR_GSEL_LO];
    assign f_smask  = instr[tse_isa_pkg::SIG_MASK_HI:tse_isa_pkg::SIG_MASK_LO];
    assign f_sval   = instr[tse_isa_pkg::SIG_VAL_HI:tse_isa_pkg::SIG_VAL_LO];
    assign f_ecode  = instr[tse_isa_pkg::EMIT_CODE_HI:tse_isa_pkg::EMIT_CODE_LO];
    assign f_edly   = instr[tse_isa_pkg::EMIT_DELAY_HI:tse_isa_pkg::EMIT_DELAY_LO];

    function automatic logic wsel(input logic [1:0] src, input logic [2:0] idx);
        case (src)
            tse_isa_pkg::WSRC_PIN:  wsel = idx[2] ? pchg[idx[1:0]] : pin_in[idx[1:0]];
            tse_isa_pkg::WSRC_EV:   wsel = ev[idx];
            tse_isa_pkg::WSRC_FLAG: wsel = idx == tse_isa_pkg::WIDX_FLAG_GUARD ? guard : flg[idx];
            default:                wsel = idx < 3'd6 && chip_flags[idx];
        endcase
    endfunction

    logic [5:0]  n;
    logic [32:0] lm;
    logic [63:0] osr_sh, isr_sh;
    assign n      = {1'b0, f_n1} + 6'd1;
    assign lm     = (33'd1 << n) - 33'd1;
    assign osr_sh = {32'd0, osr} >> n;
    assign isr_sh = {32'd0, isr} << n;

    logic        taken, met, met2, tmo;
    logic        retire, tflag_n, aflag_n, pull_take, push_set, emit_n, cclr, gwe;
    assign guard = c_q >= gt[16*gsel +: 16];
    assign pchg  = first ? 4'd0 : pin_in ^ pin0;
    assign clock = c_q;
    assign pc_o  = pc;
    logic [7:0]  npc;
    logic [3:0]  pins_n, dirs_n;
    logic [8:0]  delay;
    logic [15:0] x_n, y_n, t_n, tc_n, mv;
    logic [31:0] osr_n, isr_n, outv, inv;
    logic [3:0]  irq_n;
    wire _unused = &{lm[32], osr_sh[63:32], isr_sh[63:32], outv[31:16], 1'b0};

    always_comb begin
        retire = 1'b0; npc = pc + 8'd1; delay = '0; tflag_n = tflag; aflag_n = aflag;
        x_n = x; y_n = y; t_n = t; tc_n = tc; osr_n = osr; isr_n = isr;
        pins_n = pin_out; dirs_n = pin_oe; irq_n = '0;
        csr_we = 1'b0; csr_re = 1'b0; csr_addr = f_csr; csr_wdata = {y, x};
        cmd_we = 1'b0; cmd = f_imm; pull_take = 1'b0; push_set = 1'b0;
        sig_we = 1'b0; sig_mask = f_smask; sig_val = f_sval; emit_n = 1'b0; cclr = 1'b0; gwe = 1'b0;
        timer_ack = 1'b0;
        outv = osr & lm[31:0];
        inv = '0; mv = '0;
        taken = 1'b0; met = 1'b0; met2 = 1'b0; tmo = 1'b0;
        if (avail && !legal) irq_n = 4'b1000;
        if (avail && legal) begin
            case (ic)
                tse_isa_pkg::I_JMP: begin
                    retire = 1'b1;
                    case (f_cond)
                        tse_isa_pkg::COND_ALWAYS: taken = 1'b1;
                        tse_isa_pkg::COND_XZ:     taken = x == 16'd0;
                        tse_isa_pkg::COND_XDEC:   begin taken = x != 16'd0; x_n = x - 16'd1; end
                        tse_isa_pkg::COND_YDEC:   begin taken = y != 16'd0; y_n = y - 16'd1; end
                        tse_isa_pkg::COND_XNEY:   taken = x != y;
                        tse_isa_pkg::COND_PIN:    taken = pin_in[jmp_pin];
                        tse_isa_pkg::COND_TOUT:   taken = tflag || aflag;
                        default:                  taken = guard;
                    endcase
                    if (taken) npc = f_target;
                end
                tse_isa_pkg::I_WAIT: begin
                    if (first) begin tflag_n = 1'b0; aflag_n = 1'b0; end
                    met  = wsel(f_src, f_idx) == f_pol;
                    met2 = f_alt && wsel(alt_cfg[2:1], alt_cfg[5:3]) == alt_cfg[0];
                    tmo  = f_to && (first ? t == 16'd0 : tc == 16'd0);
                    tc_n = (first ? t : tc) - 16'd1;
                    if (met || met2 || tmo) begin
                        retire = 1'b1; aflag_n = !met && met2; tflag_n = !met && !met2;
                        delay = {6'd0, f_wdly};
                        timer_ack = met && f_pol && f_src == tse_isa_pkg::WSRC_EV && f_idx == tse_isa_pkg::WIDX_EV_TIMER;
                    end
                end
                tse_isa_pkg::I_SET: begin
                    retire = 1'b1;
                    case (f_dst)
                        tse_isa_pkg::SETDST_PINS:    pins_n = (pin_out & ~f_imm[7:4]) | (f_imm[3:0] & f_imm[7:4]);
                        tse_isa_pkg::SETDST_PINDIRS: dirs_n = (pin_oe & ~f_imm[7:4]) | (f_imm[3:0] & f_imm[7:4]);
                        tse_isa_pkg::SETDST_X:       x_n = {8'd0, f_imm};
                        tse_isa_pkg::SETDST_Y:       y_n = {8'd0, f_imm};
                        tse_isa_pkg::SETDST_T:       t_n = {t[7:0], f_imm};
                        tse_isa_pkg::SETDST_XH:      x_n = {f_imm, x[7:0]};
                        tse_isa_pkg::SETDST_IRQ:     irq_n = f_imm[3:0];
                        default:                     cmd_we = 1'b1;
                    endcase
                end
                tse_isa_pkg::I_MOV: begin
                    retire = 1'b1; delay = {4'd0, f_mdly};
                    case (f_msrc)
                        tse_isa_pkg::MOVSRC_X:    mv = x;
                        tse_isa_pkg::MOVSRC_Y:    mv = y;
                        tse_isa_pkg::MOVSRC_PINS: mv = {12'd0, pin_in};
                        tse_isa_pkg::MOVSRC_ISR:  mv = isr[15:0];
                        tse_isa_pkg::MOVSRC_OSR:  mv = osr[15:0];
                        tse_isa_pkg::MOVSRC_T:    mv = t;
                        tse_isa_pkg::MOVSRC_NOTX: mv = ~x;
                        default:                  mv = c_q;
                    endcase
                    case (f_dst)
                        tse_isa_pkg::MOVDST_X:       x_n = mv;
                        tse_isa_pkg::MOVDST_Y:       y_n = mv;
                        tse_isa_pkg::MOVDST_PINS:    pins_n = mv[3:0];
                        tse_isa_pkg::MOVDST_PINDIRS: dirs_n = mv[3:0];
                        tse_isa_pkg::MOVDST_ISR:     isr_n = {16'd0, mv};
                        tse_isa_pkg::MOVDST_OSR:     osr_n = {16'd0, mv};
                        tse_isa_pkg::MOVDST_T:       t_n = mv;
                        default:                     npc = mv[7:0];
                    endcase
                end
                tse_isa_pkg::I_OUT: begin
                    retire = 1'b1; delay = {6'd0, f_dly3}; osr_n = osr_sh[31:0];
                    case (f_dst)
                        tse_isa_pkg::OUTDST_PINS:    pins_n = (pin_out & ~lm[3:0]) | outv[3:0];
                        tse_isa_pkg::OUTDST_PINDIRS: dirs_n = (pin_oe & ~lm[3:0]) | outv[3:0];
                        tse_isa_pkg::OUTDST_X:       x_n = outv[15:0];
                        tse_isa_pkg::OUTDST_Y:       y_n = outv[15:0];
                        tse_isa_pkg::OUTDST_T:       t_n = outv[15:0];
                        default: ;
                    endcase
                end
                tse_isa_pkg::I_IN: begin
                    retire = 1'b1; delay = {6'd0, f_dly3};
                    case (f_isrc)
                        tse_isa_pkg::INSRC_PINS: inv = {28'd0, pin_in};
                        tse_isa_pkg::INSRC_X:    inv = {16'd0, x};
                        tse_isa_pkg::INSRC_Y:    inv = {16'd0, y};
                        tse_isa_pkg::INSRC_ONES: inv = 32'hffff_ffff;
                        tse_isa_pkg::INSRC_OSR:  inv = osr;
                        default:                 inv = '0;
                    endcase
                    isr_n = isr_sh[31:0] | (inv & lm[31:0]);
                end
                tse_isa_pkg::I_PUSH:
                    if (!(h_rx_full && f_blk)) begin
                        retire = 1'b1; isr_n = '0; push_set = !h_rx_full;
                    end
                tse_isa_pkg::I_PULL:
                    if (h_tx_full) begin
                        retire = 1'b1; osr_n = h_tx_data_q; pull_take = 1'b1;
                    end else if (!f_blk) begin
                        retire = 1'b1; osr_n = {16'd0, x};
                    end
                tse_isa_pkg::I_CSRW: begin
                    retire = 1'b1; csr_we = 1'b1;
                    if (tse_regs_pkg::lane_reg_class(f_csr) == tse_regs_pkg::RC_L)
                        delay = 9'(tse_isa_pkg::CSRW_CYC_CFGWORD);
                end
                tse_isa_pkg::I_CSRR: begin
                    retire = 1'b1; csr_re = 1'b1; {y_n, x_n} = csr_rdata;
                end
                tse_isa_pkg::I_STW, tse_isa_pkg::I_LDW: retire = 1'b1;
                tse_isa_pkg::I_DLY:  begin retire = 1'b1; delay = f_d9; end
                tse_isa_pkg::I_TMR:  begin retire = 1'b1; delay = {4'd0, f_tdly}; cclr = f_cclr; gwe = f_gwe; end
                tse_isa_pkg::I_SIG:  begin retire = 1'b1; sig_we = 1'b1; end
                tse_isa_pkg::I_EMIT: begin retire = 1'b1; delay = {6'd0, f_edly}; emit_n = 1'b1; end
                default: retire = 1'b1;
            endcase
            if (first && side_en) begin
                pins_n[ss_base]        = ss[0];
                pins_n[ss_base + 2'd1] = ss[1];
            end
        end
    end

    // data window: the dispatch cycle, then any wait for the data slot
    assign dacc       = avail && (ic == tse_isa_pkg::I_STW || ic == tse_isa_pkg::I_LDW);
    assign dmem_req   = dacc || dpend;
    assign dmem_we    = dpend ? dwe_q : ic == tse_isa_pkg::I_STW;
    assign dmem_addr  = dpend ? daddr_q : f_ind ? x[6:0] : f_a7;
    assign dmem_wdata = isr;

    // Fetch: the word the next dispatch needs (nw) on a miss. lru names the entry
    // not used by the latest dispatch; a fill replaces it (or an empty entry), so
    // the word of the current instruction always stays.
    // fill replaces: never the one holding the next or the current instruction.
    logic [6:0] nw;
    logic       nw_cov, vic, lru;
    assign nw     = avail ? npc[7:1] : pc[7:1];
    assign nw_cov = (e_val[0] && e_tag[0] == nw) || (e_val[1] && e_tag[1] == nw) || (fresh && f_tag == nw);
    assign imem_req  = run && !nw_cov;
    assign imem_addr = nw;

    always_comb
        if      (!e_val[0]) vic = 1'b0;
        else if (!e_val[1]) vic = 1'b1;
        else                vic = lru;
    assign aflag_o = aflag;
    assign tflag_o = tflag;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            pc <= '0; held <= 1'b0; dly <= '0; fresh <= 1'b0; f_tag <= '0; e_val <= '0; lru <= 1'b0;
            e_tag[0] <= '0; e_tag[1] <= '0; e_word[0] <= '0; e_word[1] <= '0;
            x <= '0; y <= '0; t <= '0; tc <= '0; osr <= '0; isr <= '0; tflag <= 1'b0; aflag <= 1'b0;
            c_q <= '0; gsel <= '0; pin0 <= '0; halted <= 1'b0; dpend <= 1'b0; dld <= 1'b0; dwe_q <= 1'b0; daddr_q <= '0;
            pin_out <= '0; pin_oe <= '0; irq <= '0; emit_v <= 1'b0; emit_code <= '0; emit_data <= '0;
            h_tx_full <= 1'b0; h_rx_full <= 1'b0; h_rx_data <= '0;
        end else begin
            irq <= irq_n;
            emit_v <= emit_n;
            if (emit_n) begin emit_code <= f_ecode; emit_data <= x[7:0]; end
            if (!run) begin
                pc <= start_pc; held <= 1'b0; dly <= '0; fresh <= 1'b0; e_val <= '0; c_q <= '0; gsel <= '0;
                dpend <= 1'b0; dld <= 1'b0; halted <= 1'b0;
            end else begin
                if (cclr)                 c_q <= '0;
                else if (c_q != 16'hffff) c_q <= c_q + 16'd1;
                if (gwe) gsel <= f_gsel;
                if (avail && first) pin0 <= pin_in;
                fresh <= imem_req && imem_gnt;
                f_tag <= imem_addr;
                if (fresh) begin
                    e_word[vic] <= imem_rdata; e_tag[vic] <= f_tag; e_val[vic] <= 1'b1;
                end
                if (avail && first) lru <= hf ? !vic : h0;
                if (retire) begin
                    pc <= npc; dly <= delay; held <= 1'b0;
                end else begin
                    if (avail) held <= 1'b1;
                    if (dly != 9'd0) dly <= dly - 9'd1;
                end
                x <= x_n; y <= y_n; t <= t_n; tc <= tc_n; osr <= osr_n; isr <= isr_n; tflag <= tflag_n; aflag <= aflag_n;
                pin_out <= pins_n; pin_oe <= dirs_n;
                if (avail && !legal) halted <= 1'b1;
                if (dacc && !dmem_gnt) begin dpend <= 1'b1; daddr_q <= dmem_addr; dwe_q <= dmem_we; end
                if (dpend && dmem_gnt) dpend <= 1'b0;
                dld <= dmem_req && dmem_gnt && !dmem_we;
                if (dld) osr <= dmem_rdata;
            end
            if (pull_take) h_tx_full <= 1'b0;
            if (h_tx_we) h_tx_full <= 1'b1;
            if (h_rx_re) h_rx_full <= 1'b0;
            if (push_set) begin h_rx_full <= 1'b1; h_rx_data <= isr; end
        end

    if (LATCH) begin : g_txl
        logic tx_en_q;
        always_ff @(posedge clk or negedge rst_n)
            if (!rst_n) tx_en_q <= 1'b0;
            else        tx_en_q <= h_tx_we;
        always_latch
            if (!rst_n)       h_tx_data_q = '0;
            else if (tx_en_q) h_tx_data_q = h_tx_data;
    end else begin : g_txf
        always_ff @(posedge clk or negedge rst_n)
            if (!rst_n)       h_tx_data_q <= '0;
            else if (h_tx_we) h_tx_data_q <= h_tx_data;
    end
endmodule
`default_nettype wire
