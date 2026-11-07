// The SRAM macros for timing proofs: their read data is free in every cycle.
// The synthesis view of tse_sram_512x32 uses two 512x16 macros; the SIM view
// keeps one 512x32.
module RM_IHPSG13_1P_512x16_c2_bm_bist (
  input logic        A_CLK, A_MEN, A_WEN, A_REN,
  input logic [8:0]  A_ADDR,
  input logic [15:0] A_DIN,
  input logic        A_DLY,
  output logic [15:0] A_DOUT,
  input logic [15:0] A_BM,
  input logic        A_BIST_CLK, A_BIST_EN, A_BIST_MEN, A_BIST_WEN, A_BIST_REN,
  input logic [8:0]  A_BIST_ADDR,
  input logic [15:0] A_BIST_DIN, A_BIST_BM
);
  (* anyseq *) logic [15:0] q;
  assign A_DOUT = q;
endmodule

module RM_IHPSG13_1P_512x32_c2_bm_bist (
  input logic        A_CLK, A_MEN, A_WEN, A_REN,
  input logic [8:0]  A_ADDR,
  input logic [31:0] A_DIN,
  input logic        A_DLY,
  output logic [31:0] A_DOUT,
  input logic [31:0] A_BM,
  input logic        A_BIST_CLK, A_BIST_EN, A_BIST_MEN, A_BIST_WEN, A_BIST_REN,
  input logic [8:0]  A_BIST_ADDR,
  input logic [31:0] A_BIST_DIN, A_BIST_BM
);
  (* anyseq *) logic [31:0] q;
  assign A_DOUT = q;
endmodule
