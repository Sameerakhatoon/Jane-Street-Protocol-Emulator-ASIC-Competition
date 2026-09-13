`default_nettype none
// Thin wrapper around the IHP macro: ties the BIST port off, A_DLY to 1 and
// writes whole words. Keep the instance name u_macro stable: config.json
// places the macro by its hierarchical path (u_top.u_pmem.u_sram.u_macro).
module tse_sram_512x32 (
    input  logic        clk,
    input  logic        men,
    input  logic        wen,
    input  logic        ren,
    input  logic [8:0]  addr,
    input  logic [31:0] din,
    output logic [31:0] dout
);
    RM_IHPSG13_1P_512x32_c2_bm_bist u_macro (
        .A_CLK(clk), .A_MEN(men), .A_WEN(wen), .A_REN(ren), .A_ADDR(addr),
        .A_DIN(din), .A_BM(32'hffff_ffff), .A_DLY(1'b1), .A_DOUT(dout),
        .A_BIST_EN(1'b0), .A_BIST_CLK(1'b0), .A_BIST_MEN(1'b0), .A_BIST_WEN(1'b0),
        .A_BIST_REN(1'b0), .A_BIST_ADDR(9'd0), .A_BIST_DIN(32'd0), .A_BIST_BM(32'd0)
    );
endmodule
`default_nettype wire
