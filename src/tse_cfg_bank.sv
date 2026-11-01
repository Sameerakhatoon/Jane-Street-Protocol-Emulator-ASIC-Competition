`default_nettype none
// Configuration bank: WORDS x 32 bits, every bit visible on q. LATCH = 1
// stores the bits in level-sensitive latches (27.6 um^2 per bit measured), LATCH = 0 in flops (71.2 um^2 per bit). Both guarantee the new value
// from edge 2 after the write is accepted (a latch shows it from edge 1).
// Write protocol, glitch-free without a clock gate:
//   edge 0: accept (we && !busy): wd_q and the word select are registered
//   edge 1: the one-hot enable flop of that word rises (latch transparent)
//   edge 2: the enable falls (latch closes); wd_q is still held
//   edge 3: earliest edge at which wd_q may change for the next write
// The enable comes straight from a flop and the data from a flop that is
// stable one cycle before, during and after the enable, so there is no
// glitch on the enable and no hold race at the closing edge.
// clr empties the whole bank (latch reset pin driven by a flop).
// MASK marks the bits that exist (to be generated from the config
// table); the others read as 0 and synthesis removes their storage. With
// READBACK = 0 the read port is left out (saves its 32:1 multiplexer).
// WD_REG = 0 leaves out wd_q: the writer then holds wdata, from a flop,
// from the accept cycle until edge 3, and the latches write straight from it.
module tse_cfg_bank #(
    parameter int WORDS = 32,
    parameter bit LATCH = 1,
    parameter bit READBACK = 1,
    parameter bit WD_REG = 1,
    parameter logic [WORDS*32-1:0] MASK = '1,
    localparam int AW = $clog2(WORDS)
) (
    input  logic              clk,
    input  logic              rst_n,
    input  logic              clr,
    input  logic              we,
    input  logic [AW-1:0]     addr,
    input  logic [31:0]       wdata,
    output logic              busy,
    input  logic [AW-1:0]     rd_addr,
    output logic [31:0]       rd_data,
    output logic [WORDS*32-1:0] q
);
    logic [31:0]      wd_q, wd;
    logic [AW-1:0]    sel_q;
    logic [WORDS-1:0] en_q;
    logic [1:0]       ph;
    logic             clr_nq;
    logic [WORDS*32-1:0] qraw;

    assign busy = ph == 2'd1 || ph == 2'd2;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            wd_q <= '0; sel_q <= '0; en_q <= '0; ph <= 2'd0; clr_nq <= 1'b1;
        end else begin
            clr_nq <= !clr;
            en_q  <= '0;
            case (ph)
                2'd1:    begin en_q[sel_q] <= 1'b1; ph <= 2'd2; end
                2'd2:    ph <= 2'd3;
                default: if (we) begin wd_q <= wdata; sel_q <= addr; ph <= 2'd1; end
                                 else ph <= 2'd0;
            endcase
        end

    assign wd = WD_REG ? wd_q : wdata;

    // In the CMOS5L flow every existing bit is the reset latch cell itself:
    // the Yosys in the flow image does not infer a latch reset from the
    // behavioural form and would fold the clear into data and enable, with a
    // tie cell on every latch. Simulation, lint and formal use the
    // behavioural form, which has the same function.
    for (genvar i = 0; i < WORDS; i++) begin : g_word
        if (LATCH) begin : g_lat
`ifdef SCL_sg13cmos5l_stdcell
            for (genvar b = 0; b < 32; b++) begin : g_bit
                if (MASK[32*i + b]) begin : g_on
                    sg13cmos5l_dlhrq_1 u_l (.GATE(en_q[i]), .RESET_B(clr_nq), .D(wd[b]), .Q(qraw[32*i + b]));
                end else begin : g_off
                    assign qraw[32*i + b] = 1'b0;
                end
            end
`else
            always_latch
                if (!clr_nq)      qraw[32*i +: 32] = '0;
                else if (en_q[i]) qraw[32*i +: 32] = wd;
`endif
        end else begin : g_ff
            always_ff @(posedge clk)
                if (!clr_nq)      qraw[32*i +: 32] <= '0;
                else if (en_q[i]) qraw[32*i +: 32] <= wd;
        end
    end

`ifdef SCL_sg13cmos5l_stdcell
    wire _unused_en = &{1'b0, en_q};    // words without bits have no cells
`endif
    assign q       = qraw & MASK;
    assign rd_data = READBACK ? q[32*rd_addr +: 32] : 32'd0;
endmodule
`default_nettype wire
