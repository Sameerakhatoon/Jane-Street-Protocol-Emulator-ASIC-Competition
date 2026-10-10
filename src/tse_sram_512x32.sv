`default_nettype none
// Thin wrapper around the IHP macro: A_DLY tied to 1, whole-word writes on
// both ports. The functional port serves lanes and host; the test port
// (selected by t_en, which must come from a flop) serves the self-test, so
// the self-test adds no multiplexer to the functional address path. Keep the
// instance name u_macro stable: config.json places the macro by its
// hierarchical path (u_top.u_pmem.u_sram.u_macro).
module tse_sram_512x32 (
    input  logic        clk,
    input  logic        men,
    input  logic        wen,
    input  logic        ren,
    input  logic [8:0]  addr,
    input  logic [31:0] din,
    input  logic        t_en,
    input  logic        t_men,
    input  logic        t_wen,
    input  logic [8:0]  t_addr,
    input  logic [31:0] t_din,
    output logic [31:0] dout
);
    RM_IHPSG13_1P_512x32_c2_bm_bist u_macro (
        .A_CLK(clk), .A_MEN(men), .A_WEN(wen), .A_REN(ren), .A_ADDR(addr),
        .A_DIN(din), .A_BM(32'hffff_ffff), .A_DLY(1'b1), .A_DOUT(dout),
        .A_BIST_EN(t_en), .A_BIST_CLK(clk), .A_BIST_MEN(t_men), .A_BIST_WEN(t_wen),
        .A_BIST_REN(t_men && !t_wen), .A_BIST_ADDR(t_addr), .A_BIST_DIN(t_din),
        .A_BIST_BM(32'hffff_ffff)
    );
endmodule
`default_nettype wire
