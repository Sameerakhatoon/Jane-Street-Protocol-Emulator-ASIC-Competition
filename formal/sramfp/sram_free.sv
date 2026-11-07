// SPDX-License-Identifier: Apache-2.0
`default_nettype none
// Formal stand-in for one 512x16 program SRAM macro, same ports. The program
// store is two of them (u_macro_lo, u_macro_hi). The read data is a free value
// in every cycle, so the proof holds for every program and every data word,
// and even for read data that changes without a read. The functional inputs
// are kept under fixed names (fp_*) so the product builder can find them in
// each instance after flattening.
module RM_IHPSG13_1P_512x16_c2_bm_bist (
    input  wire        A_CLK,
    input  wire        A_MEN,
    input  wire        A_WEN,
    input  wire        A_REN,
    input  wire [8:0]  A_ADDR,
    input  wire [15:0] A_DIN,
    input  wire        A_DLY,
    output wire [15:0] A_DOUT,
    input  wire [15:0] A_BM,
    input  wire        A_BIST_CLK,
    input  wire        A_BIST_EN,
    input  wire        A_BIST_MEN,
    input  wire        A_BIST_WEN,
    input  wire        A_BIST_REN,
    input  wire [8:0]  A_BIST_ADDR,
    input  wire [15:0] A_BIST_DIN,
    input  wire [15:0] A_BIST_BM
);
    (* anyseq *) wire [15:0] rd;
    assign A_DOUT = rd;

    (* keep *) wire        fp_men  = A_MEN;
    (* keep *) wire        fp_wen  = A_WEN;
    (* keep *) wire        fp_ren  = A_REN;
    (* keep *) wire [8:0]  fp_addr = A_ADDR;
    (* keep *) wire [15:0] fp_din  = A_DIN;

    wire _unused = &{A_CLK, A_DLY, A_BM, A_BIST_CLK, A_BIST_EN, A_BIST_MEN, A_BIST_WEN, A_BIST_REN,
                     A_BIST_ADDR, A_BIST_DIN, A_BIST_BM, 1'b0};
endmodule
`default_nettype wire
