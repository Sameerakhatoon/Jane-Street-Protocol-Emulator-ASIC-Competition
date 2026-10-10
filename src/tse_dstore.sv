`default_nettype none
// Data window store for the 4-lane build, where every SRAM slot belongs to a
// lane: WORDS x 32 bits in latches (LATCH = 1) or flops, at data window
// addresses 0 to WORDS - 1; higher words read 0 and ignore writes.
// Lane k owns the data cycles with cnt == 4k + 3, the rule of the 3-lane
// system slot, so LDW and STW timing is the same in both builds. The host
// reads in any cycle except cnt mod 4 == 3 and writes only in cycles with
// cnt mod 4 == 1. Read data (lane or host) is valid the cycle after the grant.
// Latch writes keep the configuration-bank rule (enable straight from a
// flop, data from a flop that is stable before, during and after the
// enable) with two data sources: lane writes open a word in a cycle with
// cnt mod 4 == 0 from wl_q, host writes in a cycle with cnt mod 4 == 2
// from h_wdata, and cnt[1] picks the source, so the latch input never
// changes at a closing edge. Contract: h_wdata holds from the write request
// until two cycles after its grant.
module tse_dstore #(
    parameter int LANES = 4,
    parameter int WORDS = 32,
    parameter bit LATCH = 1,
    localparam int AW = $clog2(WORDS)
) (
    input  logic                clk,
    input  logic                rst_n,
    input  logic [3:0]          cnt,
    input  logic [LANES-1:0]    d_req,
    input  logic [LANES-1:0]    d_we,
    input  logic [LANES*7-1:0]  d_addr,
    input  logic [LANES*32-1:0] d_wdata,
    output logic [LANES-1:0]    d_gnt,
    output logic [31:0]         d_rdata,
    input  logic                h_req,
    input  logic                h_we,
    input  logic [6:0]          h_addr,
    input  logic [31:0]         h_wdata,
    output logic                h_gnt,
    output logic [31:0]         h_rdata
);
    logic [6:0]       ga, ra;
    logic             gwe, l_go, h_go;
    logic [31:0]      gwd, wl_q, din;
    logic [WORDS-1:0] en_q;
    logic [WORDS*32-1:0] q;

    always_comb begin
        ga = '0; gwe = 1'b0; gwd = '0;
        for (int i = 0; i < LANES; i++) begin
            d_gnt[i] = d_req[i] && cnt == 4'(4 * i + 3);
            if (d_gnt[i]) begin ga = d_addr[7*i +: 7]; gwe = d_we[i]; gwd = d_wdata[32*i +: 32]; end
        end
    end
    assign l_go  = |d_gnt;
    assign h_gnt = h_req && (h_we ? cnt[1:0] == 2'd1 : cnt[1:0] != 2'd3);
    assign h_go  = h_gnt && h_we;
    assign din   = cnt[1] ? h_wdata : wl_q;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            wl_q <= '0; en_q <= '0; ra <= '0;
        end else begin
            en_q <= '0;
            if (l_go && gwe) begin
                wl_q <= gwd;
                if (32'(ga) < WORDS) en_q[AW'(ga)] <= 1'b1;
            end
            if (h_go && 32'(h_addr) < WORDS) en_q[AW'(h_addr)] <= 1'b1;
            if (l_go)                ra <= ga;
            else if (h_gnt && !h_we) ra <= h_addr;
        end

    for (genvar i = 0; i < WORDS; i++) begin : g_word
        if (LATCH) begin : g_lat
            always_latch
                if (en_q[i]) q[32*i +: 32] = din;
        end else begin : g_ff
            always_ff @(posedge clk)
                if (en_q[i]) q[32*i +: 32] <= din;
        end
    end

    assign d_rdata = 32'(ra) < WORDS ? q[32*AW'(ra) +: 32] : 32'd0;
    assign h_rdata = d_rdata;
endmodule
`default_nettype wire
