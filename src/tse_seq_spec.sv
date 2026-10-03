`default_nettype none
// Executable, cycle-exact specification of one lane sequencer: when each
// instruction dispatches, the program counter, the two-word fetch buffer under
// the fixed fetch slots, data slots for LDW and STW, WAIT with its own and its
// alternate condition and the timeout, blocking PUSH and PULL, the clock C with
// the guards, and the 3-cycle CSRW to configuration words. Every cost comes from
// the generated packages. Simulation only: it is the reference the RTL
// sequencer must match cycle by cycle.
//
// Cycle 0 is the first cycle after rst. The instruction at pc arrives on instr
// combinationally (the fetch rule decides only when it may dispatch). The spec
// keeps the control state (pc, X, Y, T, C, gsel, TFLAG, AFLAG); every other
// datapath value an instruction reads (PINS, ISR, OSR, CSRR data) comes in on
// dval in its dispatch cycle. Waitable signals come in on pins, ev, flag and
// chip; FLAG GUARD and ONE, PIN changes and the reserved CHIP indices are
// computed here. LANE_CFG, GUARD01 and GUARD23 load from cfg_* during rst and
// follow CSRW, visible LAT_CFG_WRITE_VISIBLE cycles after the dispatch.
module tse_seq_spec #(
    parameter int LANE = 0,
    parameter int FETCH_LATENCY = tse_isa_pkg::FETCH_LATENCY
) (
    input  logic        clk,
    input  logic        rst,
    input  logic [7:0]  start_pc,
    input  logic [31:0] cfg_lane,
    input  logic [31:0] cfg_guard01,
    input  logic [31:0] cfg_guard23,
    output logic [7:0]  pc,
    input  logic [15:0] instr,
    input  logic [3:0]  pins,
    input  logic [7:0]  ev,
    input  logic [7:0]  flag,
    input  logic [7:0]  chip,
    input  logic        hrx_full,
    input  logic        htx_full,
    input  logic [31:0] dval,
    output logic        issue,
    output logic        fetch,
    output logic [6:0]  fetch_word,
    output logic        halted,
    output logic [15:0] x,
    output logic [15:0] y,
    output logic [15:0] t,
    output logic [15:0] c,
    output logic        tflag,
    output logic        aflag,
    output logic        cfg_we,
    output logic [7:0]  cfg_addr,
    output logic [31:0] cfg_wdata
);
    import tse_isa_pkg::*;
    import tse_regs_pkg::*;

    if (FETCH_ENTRIES != 2 || LAT_CFG_WRITE_VISIBLE != 2) begin : g_model
        $error("tse_seq_spec models two fetch entries and config visible 2 cycles after CSRW");
    end

    localparam logic [1:0] MY_SLOT  = 2'(LANE % FRAME_SLOTS);
    localparam logic [3:0] MY_DSLOT = 4'((FRAME_SLOTS * LANE + FRAME_SLOTS - 1) % DATA_FRAME);

    // state
    logic [3:0]  cyc;                 // cycle number mod 16 (slot phase)
    logic [6:0]  tag_mru, tag_lru;    // fetch buffer; the MRU word holds pc's predecessor
    logic        lru_ok;
    logic        freq;                // pc's word requested, not read yet
    logic [1:0]  fwait;               // cycles until a read word may dispatch
    logic [16:0] ex_left;             // cycles still occupied by the current instruction
    logic        in_wait;             // current instruction waits for its end condition
    logic [15:0] wq;                  // that instruction's word
    logic [15:0] tcnt;                // WAIT timeout countdown
    logic [3:0]  snap, chg_q;         // PIN levels at WAIT dispatch, changes since
    logic [1:0]  gsel;
    logic [31:0] lane_cfg, guard01, guard23;
    logic        p_we;                // CSRW to a configuration word in flight
    logic [7:0]  p_addr;
    logic [31:0] p_data;

    // decode of the instruction at pc
    iclass_t     ic;
    logic        legal, ready, halt_now;
    assign ic       = tse_decode(instr);
    assign legal    = tse_legal(instr);
    assign ready    = !halted && !in_wait && ex_left == 17'd0 && !freq && fwait == 2'd0;
    assign issue    = ready && legal;
    assign halt_now = ready && !legal;

    // waitable signals in this cycle
    logic [3:0]  chg;
    logic [15:0] gt;
    logic        guard;
    assign chg   = issue ? 4'd0 : chg_q | (pins ^ snap);
    always_comb begin
        case (gsel)
            2'd0:    gt = guard01[LR_GUARD01_GT0_HI:LR_GUARD01_GT0_LO];
            2'd1:    gt = guard01[LR_GUARD01_GT1_HI:LR_GUARD01_GT1_LO];
            2'd2:    gt = guard23[LR_GUARD23_GT2_HI:LR_GUARD23_GT2_LO];
            default: gt = guard23[LR_GUARD23_GT3_HI:LR_GUARD23_GT3_LO];
        endcase
    end
    assign guard = c >= gt;

    function automatic logic sig(input logic [1:0] src, input logic [2:0] idx, input logic [3:0] lv,
                                 input logic [3:0] ch, input logic [7:0] e, input logic [7:0] f,
                                 input logic [7:0] cf, input logic g);
        case (src)
            WSRC_PIN:  return idx[2] ? ch[idx[1:0]] : lv[idx[1:0]];
            WSRC_EV:   return e[idx];
            WSRC_FLAG: return idx == WIDX_FLAG_GUARD ? g : idx == WIDX_FLAG_ONE ? 1'b1 : f[idx];
            default:   return idx > WIDX_CHIP_TICK ? 1'b0 : cf[idx];
        endcase
    endfunction

    // end condition of the waiting instruction (the one at pc in its dispatch cycle)
    logic [15:0] ww;
    logic [15:0] tleft;
    logic        e_own, e_alt, e_to, w_end;
    assign ww    = issue ? instr : wq;
    assign tleft = issue ? t : tcnt;
    always_comb begin
        e_own = 1'b0; e_alt = 1'b0; e_to = 1'b0;
        case (tse_decode(ww))
            I_WAIT: begin
                e_own = sig(ww[WAIT_SRC_HI:WAIT_SRC_LO], ww[WAIT_IDX_HI:WAIT_IDX_LO], pins, chg, ev, flag,
                            chip, guard) == ww[WAIT_POL_HI];
                e_alt = ww[WAIT_ALT_HI] && sig(lane_cfg[LR_LANE_CFG_ALT_SRC_HI:LR_LANE_CFG_ALT_SRC_LO],
                                               lane_cfg[LR_LANE_CFG_ALT_IDX_HI:LR_LANE_CFG_ALT_IDX_LO],
                                               pins, chg, ev, flag, chip, guard)
                        == lane_cfg[LR_LANE_CFG_ALT_POL_HI];
                e_to  = ww[WAIT_TO_HI] && tleft == 16'd0;
            end
            I_PUSH:  e_own = !hrx_full;
            I_PULL:  e_own = htx_full;
            default: ;
        endcase
    end
    assign w_end = e_own || e_alt || e_to;

    // successor and the dispatching instruction's effects
    logic [7:0]  npc;
    logic        taken;
    logic [15:0] mval, oval;
    logic [4:0]  nbits;
    logic [16:0] dur;
    logic [3:0]  ds_wait;
    logic        cfg_word;
    assign cfg_word = ic == I_CSRW && lane_reg_class(instr[CSRW_ADDR_HI:CSRW_ADDR_LO]) == RC_L;
    assign ds_wait  = MY_DSLOT - cyc;           // s - c for the next data slot s >= c
    always_comb begin
        case (instr[JMP_COND_HI:JMP_COND_LO])
            COND_ALWAYS: taken = 1'b1;
            COND_XZ:     taken = x == 16'd0;
            COND_XDEC:   taken = x != 16'd0;
            COND_YDEC:   taken = y != 16'd0;
            COND_XNEY:   taken = x != y;
            COND_PIN:    taken = pins[lane_cfg[LR_LANE_CFG_JMP_PIN_HI:LR_LANE_CFG_JMP_PIN_LO]];
            COND_TOUT:   taken = tflag || aflag;
            default:     taken = guard;
        endcase
        case (instr[MOV_SRC_HI:MOV_SRC_LO])
            MOVSRC_X:    mval = x;
            MOVSRC_Y:    mval = y;
            MOVSRC_T:    mval = t;
            MOVSRC_NOTX: mval = ~x;
            MOVSRC_C:    mval = c;
            default:     mval = dval[15:0];
        endcase
        nbits = instr[OUT_N_HI:OUT_N_LO];       // n - 1
        oval  = nbits >= 5'd15 ? dval[15:0] : dval[15:0] & 16'((17'd1 << (nbits + 5'd1)) - 17'd1);
        npc = pc + 8'd1;
        if (ic == I_JMP && taken)
            npc = instr[JMP_TARGET_HI:JMP_TARGET_LO];
        if (ic == I_MOV && instr[MOV_DST_HI:MOV_DST_LO] == MOVDST_PC)
            npc = mval[7:0];
        case (tse_dslot(instr))
            DS_STORE: dur = 17'(ds_wait) + 17'd1;
            DS_LOAD:  dur = 17'(ds_wait) + 17'd2 + 17'(FETCH_LATENCY - 1);
            default:  dur = 17'(1 + tse_delay(instr)) + (cfg_word ? 17'(tse_cfgword(instr)) : 17'd0);
        endcase
    end

    // fetch rule: hit if npc's word is buffered, else read it in the first own slot
    logic [6:0] nw;
    logic       hit, miss;
    assign nw         = npc[7:1];
    assign hit        = nw == tag_mru || (lru_ok && nw == tag_lru);
    assign miss       = issue && !hit;
    assign fetch      = (freq || miss) && cyc[1:0] == MY_SLOT;
    assign fetch_word = issue ? nw : tag_mru;
    assign cfg_we     = issue && cfg_word;
    assign cfg_addr   = instr[CSRW_ADDR_HI:CSRW_ADDR_LO];
    assign cfg_wdata  = {y, x};

    always_ff @(posedge clk) begin
        if (rst) begin
            cyc <= 4'd0; pc <= start_pc; tag_mru <= start_pc[7:1]; tag_lru <= 7'd0; lru_ok <= 1'b0;
            freq <= 1'b1; fwait <= 2'd0; ex_left <= 17'd0; in_wait <= 1'b0; wq <= 16'd0; tcnt <= 16'd0;
            snap <= 4'd0; chg_q <= 4'd0; gsel <= 2'd0; halted <= 1'b0;
            x <= 16'd0; y <= 16'd0; t <= 16'd0; c <= 16'd0; tflag <= 1'b0; aflag <= 1'b0;
            lane_cfg <= cfg_lane & LR_LANE_CFG_MASK;
            guard01 <= cfg_guard01 & LR_GUARD01_MASK;
            guard23 <= cfg_guard23 & LR_GUARD23_MASK;
            p_we <= 1'b0; p_addr <= 8'd0; p_data <= 32'd0;
        end else begin
            cyc   <= cyc + 4'd1;
            c     <= c == 16'hFFFF ? c : c + 16'd1;
            chg_q <= chg;
            if (halt_now)
                halted <= 1'b1;

            // fetch buffer and the word on its way
            if (fetch) begin
                freq  <= 1'b0;
                fwait <= 2'(FETCH_LATENCY - 1);
            end else begin
                if (miss) freq <= 1'b1;
                if (fwait != 2'd0) fwait <= fwait - 2'd1;
            end
            if (issue && nw != tag_mru) begin
                tag_mru <= nw;
                tag_lru <= tag_mru;
                lru_ok  <= 1'b1;
            end

            // configuration seen by the sequencer, visible from dispatch + 2
            p_we <= cfg_we; p_addr <= cfg_addr; p_data <= cfg_wdata;
            if (p_we) begin
                if (p_addr == LR_LANE_CFG) lane_cfg <= p_data & LR_LANE_CFG_MASK;
                if (p_addr == LR_GUARD01)  guard01  <= p_data & LR_GUARD01_MASK;
                if (p_addr == LR_GUARD23)  guard23  <= p_data & LR_GUARD23_MASK;
            end

            // occupation of the current instruction
            if (in_wait) begin
                tcnt <= tleft - 16'd1;
                if (w_end) begin
                    in_wait <= 1'b0;
                    ex_left <= 17'd0;
                    if (tse_decode(wq) == I_WAIT) begin
                        ex_left <= 17'(wq[WAIT_DELAY_HI:WAIT_DELAY_LO]);
                        tflag   <= !e_own && !e_alt;
                        aflag   <= !e_own && e_alt;
                    end
                end
            end else if (ex_left != 17'd0) begin
                ex_left <= ex_left - 17'd1;
            end

            if (issue) begin
                pc <= npc;
                wq <= instr;
                ex_left <= dur - 17'd1;
                if (tse_may_wait(instr)) begin
                    tcnt <= tleft - 16'd1;
                    if (w_end) begin
                        ex_left <= ic == I_WAIT ? 17'(instr[WAIT_DELAY_HI:WAIT_DELAY_LO]) : 17'd0;
                    end else begin
                        in_wait <= 1'b1;
                        ex_left <= 17'd0;
                    end
                end
                if (ic == I_WAIT) begin
                    snap  <= pins;
                    chg_q <= 4'd0;
                    tflag <= w_end && !e_own && !e_alt;
                    aflag <= w_end && !e_own && e_alt;
                end
                case (ic)
                    I_JMP: begin
                        if (instr[JMP_COND_HI:JMP_COND_LO] == COND_XDEC) x <= x - 16'd1;
                        if (instr[JMP_COND_HI:JMP_COND_LO] == COND_YDEC) y <= y - 16'd1;
                    end
                    I_SET: case (instr[SET_DST_HI:SET_DST_LO])
                        SETDST_X:  x <= 16'(instr[SET_IMM_HI:SET_IMM_LO]);
                        SETDST_Y:  y <= 16'(instr[SET_IMM_HI:SET_IMM_LO]);
                        SETDST_T:  t <= {t[7:0], instr[SET_IMM_HI:SET_IMM_LO]};
                        SETDST_XH: x[15:8] <= instr[SET_IMM_HI:SET_IMM_LO];
                        default: ;
                    endcase
                    I_MOV: case (instr[MOV_DST_HI:MOV_DST_LO])
                        MOVDST_X: x <= mval;
                        MOVDST_Y: y <= mval;
                        MOVDST_T: t <= mval;
                        default: ;
                    endcase
                    I_OUT: case (instr[OUT_DST_HI:OUT_DST_LO])
                        OUTDST_X: x <= oval;
                        OUTDST_Y: y <= oval;
                        OUTDST_T: t <= oval;
                        default: ;
                    endcase
                    I_CSRR: begin
                        x <= dval[15:0];
                        y <= dval[31:16];
                    end
                    I_TMR: begin
                        if (instr[TMR_CCLR_HI]) c <= 16'd0;
                        if (instr[TMR_GWE_HI])  gsel <= instr[TMR_GSEL_HI:TMR_GSEL_LO];
                    end
                    default: ;
                endcase
            end
        end
    end
endmodule
`default_nettype wire
