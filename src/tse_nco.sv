`default_nettype none
// Numerically controlled oscillator: adds inc every cycle, ticks on carry.
// Tick rate = f_clk * inc / 2^W, so any rate is reachable at 60 or 50 MHz.
module tse_nco #(
    parameter int W = 24
) (
    input  logic         clk,
    input  logic         rst_n,
    input  logic         en,
    input  logic [W-1:0] inc,
    output logic         tick
);
    logic [W-1:0] acc;
    logic [W:0]   sum;
    assign sum = {1'b0, acc} + {1'b0, inc};
    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n)   begin acc <= '0; tick <= 1'b0; end
        else if (!en) begin acc <= '0; tick <= 1'b0; end
        else          begin acc <= sum[W-1:0]; tick <= sum[W]; end
endmodule
`default_nettype wire
