`default_nettype none
// Thin wrapper around the IHP SRAM. The 512x32 program store is built from two
// 512x16 single-port macros that share the address and the control and hold the
// low and high halves of each word (u_macro_lo bits 15:0, u_macro_hi bits
// 31:16). Two narrower macros present a 236.8 um routing wall each instead of
// one 416.64 um wall, so horizontal routing has more room above the memory. The
// halves take the same address, the same men/wen/ren and a whole-word bit mask,
// so the pair behaves exactly as one 512x32 with one-cycle read latency. A_DLY
// is tied to 1 and writes are whole-word on both the functional and the test
// port; the test port (selected by t_en, which must come from a flop) serves the
// self-test, so the self-test adds no multiplexer to the functional address
// path. Keep the instance names stable: src/config.json places the two macros
// by their hierarchical paths (u_top.u_pmem.u_sram.u_macro_lo and ...u_macro_hi).
// In simulation one 512x32 behavioural model stands in for the pair as the
// golden reference (bit-for-bit identical function) and keeps the name u_macro
// so the fault-injection and BIST test hooks reach it unchanged.
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
`ifdef SIM
    RM_IHPSG13_1P_512x32_c2_bm_bist u_macro (
        .A_CLK(clk), .A_MEN(men), .A_WEN(wen), .A_REN(ren), .A_ADDR(addr),
        .A_DIN(din), .A_BM(32'hffff_ffff), .A_DLY(1'b1), .A_DOUT(dout),
        .A_BIST_EN(t_en), .A_BIST_CLK(clk), .A_BIST_MEN(t_men), .A_BIST_WEN(t_wen),
        .A_BIST_REN(t_men && !t_wen), .A_BIST_ADDR(t_addr), .A_BIST_DIN(t_din),
        .A_BIST_BM(32'hffff_ffff)
    );
`else
    RM_IHPSG13_1P_512x16_c2_bm_bist u_macro_lo (
        .A_CLK(clk), .A_MEN(men), .A_WEN(wen), .A_REN(ren), .A_ADDR(addr),
        .A_DIN(din[15:0]), .A_BM(16'hffff), .A_DLY(1'b1), .A_DOUT(dout[15:0]),
        .A_BIST_EN(t_en), .A_BIST_CLK(clk), .A_BIST_MEN(t_men), .A_BIST_WEN(t_wen),
        .A_BIST_REN(t_men && !t_wen), .A_BIST_ADDR(t_addr), .A_BIST_DIN(t_din[15:0]),
        .A_BIST_BM(16'hffff)
    );
    RM_IHPSG13_1P_512x16_c2_bm_bist u_macro_hi (
        .A_CLK(clk), .A_MEN(men), .A_WEN(wen), .A_REN(ren), .A_ADDR(addr),
        .A_DIN(din[31:16]), .A_BM(16'hffff), .A_DLY(1'b1), .A_DOUT(dout[31:16]),
        .A_BIST_EN(t_en), .A_BIST_CLK(clk), .A_BIST_MEN(t_men), .A_BIST_WEN(t_wen),
        .A_BIST_REN(t_men && !t_wen), .A_BIST_ADDR(t_addr), .A_BIST_DIN(t_din[31:16]),
        .A_BIST_BM(16'hffff)
    );
`endif
endmodule
`default_nettype wire
