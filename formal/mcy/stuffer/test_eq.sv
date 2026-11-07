// SPDX-License-Identifier: Apache-2.0
// MCY miter: the original tse_stuffer (mutsel = 0) against the mutant
// (mutsel = 1), same inputs, every output. Legal configurations only, the
// same ones the kill test assumes: n from 1 to 15, at least one polarity,
// constant during a run.
module miter (
  input logic       clk,
  input logic       clear, tx_step, tx_din, rx_step, rx_din
);
  (* anyconst *) logic [3:0] n;
  (* anyconst *) logic [1:0] mode;
  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;
  logic [5:0] r, u;
  tse_stuffer ref_i (
    .mutsel(1'b0), .clk, .rst_n(!init), .cfg_n(n), .cfg_mode(mode), .clear, .tx_step, .tx_din,
    .tx_take(r[0]), .tx_dout(r[1]), .tx_pending(r[2]), .rx_step, .rx_din, .rx_valid(r[3]),
    .rx_dout(r[4]), .rx_err(r[5]));
  tse_stuffer uut_i (
    .mutsel(1'b1), .clk, .rst_n(!init), .cfg_n(n), .cfg_mode(mode), .clear, .tx_step, .tx_din,
    .tx_take(u[0]), .tx_dout(u[1]), .tx_pending(u[2]), .rx_step, .rx_din, .rx_valid(u[3]),
    .rx_dout(u[4]), .rx_err(u[5]));
  always_comb assume (n != 4'd0 && mode != 2'd0);
  always_comb if (!init) assert (r == u);
endmodule
