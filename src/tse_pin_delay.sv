`default_nettype none
// Programmable input sample delay in half cycles, 0 to 7, per lane input.
// in_p is the rising-edge sample and in_n the falling-edge sample taken half
// a cycle before it (so in_n is half a cycle older). Even delays take in_p
// delayed by d/2 cycles, odd delays take in_n delayed by (d-1)/2 cycles, so
// the output shows the pin d/2 cycles later than with delay 0. Used to line
// up a sample point with the pad round trip (for example SPI controller MISO).
// Only the sample path is delayed; the DPLL half-cycle input takes in_n directly.
module tse_pin_delay #(
    parameter int N = 12
) (
    input  logic         clk,
    input  logic         rst_n,
    input  logic [N*3-1:0] cfg_dly,
    input  logic [N-1:0] in_p,
    input  logic [N-1:0] in_n,
    output logic [N-1:0] out_p
);
    logic [2:0] hp [N];
    logic [2:0] hn [N];

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            for (int i = 0; i < N; i++) begin hp[i] <= '0; hn[i] <= '0; end
        end else begin
            for (int i = 0; i < N; i++) begin
                hp[i] <= {hp[i][1:0], in_p[i]};
                hn[i] <= {hn[i][1:0], in_n[i]};
            end
        end

    always_comb
        for (int i = 0; i < N; i++) begin
            logic [2:0] d;
            d = cfg_dly[3*i +: 3];
            if (d == 3'd0)      out_p[i] = in_p[i];
            else if (d == 3'd1) out_p[i] = in_n[i];
            else if (d[0])      out_p[i] = hn[i][d[2:1] - 2'd1];
            else                out_p[i] = hp[i][d[2:1] - 2'd1];
        end
endmodule
`default_nettype wire
