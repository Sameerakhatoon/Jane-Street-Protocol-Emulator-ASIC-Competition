`default_nettype none
// One configuration word. LATCH = 1: transparent-high latches whose enable is
// a flop output, so the enable cannot glitch and no clock-gate cell is needed.
// LATCH = 0: plain flops, same visible timing.
// Contract for the writer: we is high in one cycle c and wdata is stable
// during cycles c, c+1 and c+2. Both versions show the new value from c+1.
// clr_n must come from a flop (it is the latch reset), never from a pin.
module tse_cfg_word #(
    parameter int W     = 32,
    parameter bit LATCH = 1
) (
    input  logic         clk,
    input  logic         clr_n,
    input  logic         we,
    input  logic [W-1:0] wdata,
    output logic [W-1:0] q
);
    if (LATCH) begin : g_lat
        logic en_q;
        always_ff @(posedge clk or negedge clr_n)
            if (!clr_n) en_q <= 1'b0;
            else        en_q <= we;
        always_latch
            if (!clr_n)    q = '0;
            else if (en_q) q = wdata;
    end else begin : g_ff
        always_ff @(posedge clk or negedge clr_n)
            if (!clr_n)  q <= '0;
            else if (we) q <= wdata;
    end
endmodule
`default_nettype wire
