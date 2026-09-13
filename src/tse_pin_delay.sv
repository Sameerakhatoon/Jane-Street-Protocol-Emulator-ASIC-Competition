`default_nettype none
// Programmable input sample delay: each of N synchronized lane inputs (both
// the rising-edge and the falling-edge sample) can be delayed by 0 to D-1
// extra clock cycles, chosen per input by config. Used to line up a sample
// point with the pad round trip (for example SPI controller MISO).
module tse_pin_delay #(
    parameter int N = 12,
    parameter int D = 8,
    localparam int DW = $clog2(D)
) (
    input  logic          clk,
    input  logic          rst_n,
    input  logic [N*DW-1:0] cfg_dly,
    input  logic [N-1:0]  in_p,
    input  logic [N-1:0]  in_n,
    output logic [N-1:0]  out_p,
    output logic [N-1:0]  out_n
);
    logic [D-2:0] hp [N];
    logic [D-2:0] hn [N];

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            for (int i = 0; i < N; i++) begin hp[i] <= '0; hn[i] <= '0; end
        end else begin
            for (int i = 0; i < N; i++) begin
                hp[i] <= {hp[i][D-3:0], in_p[i]};
                hn[i] <= {hn[i][D-3:0], in_n[i]};
            end
        end

    always_comb
        for (int i = 0; i < N; i++) begin
            logic [DW-1:0] d;
            d = cfg_dly[DW*i +: DW];
            out_p[i] = (d == '0) ? in_p[i] : hp[i][d - DW'(1)];
            out_n[i] = (d == '0) ? in_n[i] : hn[i][d - DW'(1)];
        end
endmodule
`default_nettype wire
