// SPDX-License-Identifier: Apache-2.0
// MCY miter: the original tse_crc (mutsel = 0) against the mutant
// (mutsel = 1) on the same inputs, every output. The configuration is free
// but constant during a run, as configuration words are.
module miter (
  input logic        clk,
  input logic        clear, bit_valid, bit_in, byte_valid, tx_load, tx_shift,
  input logic [7:0]  byte_in
);
  (* anyconst *) logic [31:0] poly, ini, xo, check;
  (* anyconst *) logic [4:0]  wm1;
  (* anyconst *) logic        refin, refout;
  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;
  logic        r_busy, r_tx, r_ok, u_busy, u_tx, u_ok;
  logic [31:0] r_res, u_res;
  tse_crc ref_i (
    .mutsel(1'b0), .clk, .rst_n(!init), .cfg_poly(poly), .cfg_init(ini), .cfg_xo(xo),
    .cfg_check(check), .cfg_wm1(wm1), .cfg_refin(refin), .cfg_refout(refout), .clear, .bit_valid,
    .bit_in, .byte_valid, .byte_in, .busy(r_busy), .tx_load, .tx_shift, .tx_bit(r_tx),
    .result(r_res), .check_ok(r_ok));
  tse_crc uut_i (
    .mutsel(1'b1), .clk, .rst_n(!init), .cfg_poly(poly), .cfg_init(ini), .cfg_xo(xo),
    .cfg_check(check), .cfg_wm1(wm1), .cfg_refin(refin), .cfg_refout(refout), .clear, .bit_valid,
    .bit_in, .byte_valid, .byte_in, .busy(u_busy), .tx_load, .tx_shift, .tx_bit(u_tx),
    .result(u_res), .check_ok(u_ok));
  always_comb if (!init)
    assert (r_busy == u_busy && r_tx == u_tx && r_ok == u_ok && r_res == u_res);
endmodule
