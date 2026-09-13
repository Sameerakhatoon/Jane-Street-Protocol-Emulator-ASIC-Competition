`default_nettype none
// Two-flop synchronizer for W independent asynchronous inputs.
// Only use it on signals where each bit may be seen one cycle early or late.
module tse_sync #(
    parameter int W = 1
) (
    input  logic         clk,
    input  logic         rst_n,
    input  logic [W-1:0] d,
    output logic [W-1:0] q
);
    logic [W-1:0] meta;
    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) {q, meta} <= '0;
        else        {q, meta} <= {meta, d};
endmodule
`default_nettype wire
