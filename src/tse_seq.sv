`default_nettype none
// One lane sequencer (DLY is the default opcode). 16-bit instructions: [15:13] op, [12:11] side-set, [10:0] arg.
//   JMP  cond[10:8] target[7:0]   cond: always, X==0, X--!=0, Y--!=0, X!=Y, pin, TOUT (TFLAG|AFLAG), event
//   WAIT pol[10] src[9:8] idx[7:5] to[4] alt[3] delay[2:0]
//        src: 0 pin, 1 event, 2 flag, 3 chip (chip flags not wired yet, read 0); timeout
//        counts T cycles; alt also ends the wait on the lane's alternate condition
//        alt_cfg {idx[5:3], src[2:1], pol[0]}. Priority in one cycle: own condition
//        (flags 0), alternate (AFLAG), timeout (TFLAG).
//   SET  dst[10:8] imm[7:0]       dst: pins, pindirs, X, Y, T (byte shift-in), X[15:8], irq, cmd
//   MOV  dst[10:8] src[7:5] delay[4:0]
//   OUT  dst[10:8] n-1[7:3] delay[2:0]   OSR shifts right (LSB first)
//   IN   src[10:8] n-1[7:3] delay[2:0]   ISR shifts left (MSB first)
//   XFR  sub[10:9] blk[8] addr[7:0]      PUSH, PULL, CSRW {Y,X}, CSRR {Y,X}
//        CSRW to a config word (addr < 0x20) lasts 3 cycles, the latch bank's write period.
//   DLY  delay[10:0]
// Each instruction takes 1 + delay cycles when its word is buffered on time.
// Fetch: 32-bit words (two instructions) from a synchronous memory, data one
// cycle after a granted request, used straight from the memory output and kept
// in a two-entry buffer. A miss requests the word at once (granted in the
// lane's slot); the fill replaces the entry that does not hold the current
// instruction (least recently used).
module tse_seq (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        run,
    output logic        imem_req,
    output logic [6:0]  imem_addr,          // word address in the lane's window
    input  logic        imem_gnt,
    input  logic [31:0] imem_rdata,
    input  logic [5:0]  alt_cfg,
    input  logic [7:0]  flg,               // FLAG sources (event map)
    input  logic [7:0]  pin_in,
    output logic [7:0]  pin_out,
    output logic [7:0]  pin_oe,
    input  logic        side_en,
    input  logic [2:0]  ss_base,
    input  logic [2:0]  jmp_pin,
    input  logic [7:0]  ev,
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
    output logic        aflag_o,
    output logic        tflag_o
);
    localparam logic [2:0] OP_JMP = 3'd0, OP_WAIT = 3'd1, OP_SET = 3'd2, OP_MOV = 3'd3,
                                                 OP_OUT = 3'd4, OP_IN = 3'd5, OP_XFR = 3'd6;

    logic [7:0]  pc;
    logic [15:0] x, y, t, tc;
    logic [31:0] osr, isr;
    logic [10:0] dly;
    logic        held, tflag, aflag;
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
    logic [2:0]  op;
    logic [10:0] arg;
    logic        avail;
    assign h0    = e_val[0] && e_tag[0] == pc[7:1];
    assign h1    = e_val[1] && e_tag[1] == pc[7:1];
    assign hf    = fresh && f_tag == pc[7:1];
    assign word  = hf ? imem_rdata : h0 ? e_word[0] : e_word[1];
    assign instr = pc[0] ? word[31:16] : word[15:0];
    assign op    = instr[15:13];
    assign arg   = instr[10:0];
    assign first = !held;
    assign avail = run && (hf || h0 || h1) && dly == 11'd0;

    function automatic logic wsel(input logic [1:0] src, input logic [2:0] idx);
        case (src)
            2'd0:    wsel = pin_in[idx];
            2'd1:    wsel = ev[idx];
            2'd2:    wsel = flg[idx];
            default: wsel = 1'b0;
        endcase
    endfunction

    logic [5:0]  n;
    logic [32:0] lm;
    logic [63:0] osr_sh, isr_sh;
    assign n      = {1'b0, arg[7:3]} + 6'd1;
    assign lm     = (33'd1 << n) - 33'd1;
    assign osr_sh = {32'd0, osr} >> n;
    assign isr_sh = {32'd0, isr} << n;

    logic        retire, tflag_n, aflag_n, pull_take, push_set;
    logic [7:0]  npc, pins_n, dirs_n;
    logic [10:0] delay;
    logic [15:0] x_n, y_n, t_n, tc_n, mv;
    logic [31:0] osr_n, isr_n, outv, inv;
    logic [3:0]  irq_n;
    wire _unused = &{lm[32], osr_sh[63:32], isr_sh[63:32], outv[31:16], 1'b0};

    always_comb begin
        retire = 1'b0; npc = pc + 8'd1; delay = '0; tflag_n = tflag; aflag_n = aflag;
        x_n = x; y_n = y; t_n = t; tc_n = tc; osr_n = osr; isr_n = isr;
        pins_n = pin_out; dirs_n = pin_oe; irq_n = '0;
        csr_we = 1'b0; csr_re = 1'b0; csr_addr = arg[7:0]; csr_wdata = {y, x};
        cmd_we = 1'b0; cmd = arg[7:0]; pull_take = 1'b0; push_set = 1'b0;
        outv = osr & lm[31:0];
        inv = '0; mv = '0;
        if (avail) begin
            case (op)
                OP_JMP: begin
                    logic taken;
                    retire = 1'b1;
                    case (arg[10:8])
                        3'd0: taken = 1'b1;
                        3'd1: taken = x == 16'd0;
                        3'd2: begin taken = x != 16'd0; x_n = x - 16'd1; end
                        3'd3: begin taken = y != 16'd0; y_n = y - 16'd1; end
                        3'd4: taken = x != y;
                        3'd5: taken = pin_in[jmp_pin];
                        3'd6: taken = tflag || aflag;
                        default: taken = |ev;
                    endcase
                    if (taken) npc = arg[7:0];
                end
                OP_WAIT: begin
                    logic met, met2, tmo;
                    met  = wsel(arg[9:8], arg[7:5]) == arg[10];
                    met2 = arg[3] && wsel(alt_cfg[2:1], alt_cfg[5:3]) == alt_cfg[0];
                    tmo  = arg[4] && (first ? t == 16'd0 : tc == 16'd0);
                    tc_n = (first ? t : tc) - 16'd1;
                    if (met || met2 || tmo) begin
                        retire = 1'b1; aflag_n = !met && met2; tflag_n = !met && !met2;
                        delay = {8'd0, arg[2:0]};
                    end
                end
                OP_SET: begin
                    retire = 1'b1;
                    case (arg[10:8])
                        3'd0: pins_n = arg[7:0];
                        3'd1: dirs_n = arg[7:0];
                        3'd2: x_n = {8'd0, arg[7:0]};
                        3'd3: y_n = {8'd0, arg[7:0]};
                        3'd4: t_n = {t[7:0], arg[7:0]};
                        3'd5: x_n = {arg[7:0], x[7:0]};
                        3'd6: irq_n = arg[3:0];
                        default: cmd_we = 1'b1;
                    endcase
                end
                OP_MOV: begin
                    retire = 1'b1; delay = {6'd0, arg[4:0]};
                    case (arg[7:5])
                        3'd0: mv = x;
                        3'd1: mv = y;
                        3'd2: mv = {8'd0, pin_in};
                        3'd3: mv = isr[15:0];
                        3'd4: mv = osr[15:0];
                        3'd5: mv = t;
                        3'd6: mv = ~x;
                        default: mv = '0;
                    endcase
                    case (arg[10:8])
                        3'd0: x_n = mv;
                        3'd1: y_n = mv;
                        3'd2: pins_n = mv[7:0];
                        3'd3: dirs_n = mv[7:0];
                        3'd4: isr_n = {16'd0, mv};
                        3'd5: osr_n = {16'd0, mv};
                        3'd6: t_n = mv;
                        default: npc = mv[7:0];
                    endcase
                end
                OP_OUT: begin
                    retire = 1'b1; delay = {8'd0, arg[2:0]}; osr_n = osr_sh[31:0];
                    case (arg[10:8])
                        3'd0: pins_n = (pin_out & ~lm[7:0]) | outv[7:0];
                        3'd1: dirs_n = (pin_oe & ~lm[7:0]) | outv[7:0];
                        3'd2: x_n = outv[15:0];
                        3'd3: y_n = outv[15:0];
                        3'd5: t_n = outv[15:0];
                        default: ;
                    endcase
                end
                OP_IN: begin
                    retire = 1'b1; delay = {8'd0, arg[2:0]};
                    case (arg[10:8])
                        3'd0: inv = {24'd0, pin_in};
                        3'd1: inv = {16'd0, x};
                        3'd2: inv = {16'd0, y};
                        3'd4: inv = 32'hffff_ffff;
                        3'd5: inv = osr;
                        default: inv = '0;
                    endcase
                    isr_n = isr_sh[31:0] | (inv & lm[31:0]);
                end
                OP_XFR: begin
                    case (arg[10:9])
                        2'd0: if (!(h_rx_full && arg[8])) begin
                                        retire = 1'b1; isr_n = '0; push_set = !h_rx_full;
                                    end
                        2'd1: if (h_tx_full) begin
                                        retire = 1'b1; osr_n = h_tx_data_q; pull_take = 1'b1;
                                    end else if (!arg[8]) begin
                                        retire = 1'b1; osr_n = {16'd0, x};
                                    end
                        2'd2: begin
                                        retire = 1'b1; csr_we = 1'b1;
                                        if (arg[7:5] == 3'd0) delay = 11'd2;      // config word: 3 cycles
                                    end
                        default: begin retire = 1'b1; csr_re = 1'b1; {y_n, x_n} = csr_rdata; end
                    endcase
                end
                default: begin retire = 1'b1; delay = arg; end
            endcase
            if (first && side_en) begin
                pins_n[ss_base]        = instr[11];
                pins_n[ss_base + 3'd1] = instr[12];
            end
        end
    end

    // Fetch: the word the next dispatch needs (nw) on a miss. vic is the entry a
    // fill replaces: never the one holding the next or the current instruction.
    logic [6:0] nw;
    logic       nw_cov, vic;
    assign nw     = retire ? npc[7:1] : pc[7:1];
    assign nw_cov = (e_val[0] && e_tag[0] == nw) || (e_val[1] && e_tag[1] == nw) || (fresh && f_tag == nw);
    assign imem_req  = run && !nw_cov;
    assign imem_addr = nw;

    always_comb
        if      (e_val[0] && e_tag[0] == nw) vic = 1'b1;
        else if (e_val[1] && e_tag[1] == nw) vic = 1'b0;
        else if (h0)                         vic = 1'b1;
        else if (h1)                         vic = 1'b0;
        else                                 vic = e_val[0];
    assign aflag_o = aflag;
    assign tflag_o = tflag;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            pc <= '0; held <= 1'b0; dly <= '0; fresh <= 1'b0; f_tag <= '0; e_val <= '0;
            e_tag[0] <= '0; e_tag[1] <= '0; e_word[0] <= '0; e_word[1] <= '0;
            x <= '0; y <= '0; t <= '0; tc <= '0; osr <= '0; isr <= '0; tflag <= 1'b0; aflag <= 1'b0;
            pin_out <= '0; pin_oe <= '0; irq <= '0;
            h_tx_full <= 1'b0; h_tx_data_q <= '0; h_rx_full <= 1'b0; h_rx_data <= '0;
        end else begin
            irq <= irq_n;
            if (!run) begin
                pc <= '0; held <= 1'b0; dly <= '0; fresh <= 1'b0; e_val <= '0;
            end else begin
                fresh <= imem_req && imem_gnt;
                f_tag <= imem_addr;
                if (fresh) begin
                    e_word[vic] <= imem_rdata; e_tag[vic] <= f_tag; e_val[vic] <= 1'b1;
                end
                if (retire) begin
                    pc <= npc; dly <= delay; held <= 1'b0;
                end else begin
                    if (avail) held <= 1'b1;
                    if (dly != 11'd0) dly <= dly - 11'd1;
                end
                x <= x_n; y <= y_n; t <= t_n; tc <= tc_n; osr <= osr_n; isr <= isr_n; tflag <= tflag_n; aflag <= aflag_n;
                pin_out <= pins_n; pin_oe <= dirs_n;
            end
            if (pull_take) h_tx_full <= 1'b0;
            if (h_tx_we) begin h_tx_full <= 1'b1; h_tx_data_q <= h_tx_data; end
            if (h_rx_re) h_rx_full <= 1'b0;
            if (push_set) begin h_rx_full <= 1'b1; h_rx_data <= isr; end
        end
endmodule
`default_nettype wire
