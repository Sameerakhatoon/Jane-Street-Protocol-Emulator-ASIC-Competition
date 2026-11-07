// SPDX-License-Identifier: Apache-2.0
// MCY miter: the original tse_codec (mutsel = 0) against the mutant
// (mutsel = 1), same inputs, every output. Legal modes only (0 to 2), as
// in the kill test, constant during a run; strobes and line_set are free here.
// rx_bit only counts with rx_valid: the lane reads it only then.
module miter (
  input logic       clk,
  input logic       tx_step, tx_half, tx_bit, line_set, line_val,
  input logic       rx_sample, rx_s, rx_q1, rx_q1_s
);
  (* anyconst *) logic [1:0] mode;
  (* anyconst *) logic       inv, t_on;
  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;
  logic [3:0] r, u;
  tse_codec ref_i (
    .mutsel(1'b0), .clk, .rst_n(!init), .cfg_mode(mode), .cfg_inv(inv), .cfg_t_on(t_on), .tx_step,
    .tx_half, .tx_bit, .line_set, .line_val, .tx_line(r[0]), .rx_sample, .rx_s, .rx_q1, .rx_q1_s,
    .rx_valid(r[1]), .rx_bit(r[2]), .rx_err(r[3]));
  tse_codec uut_i (
    .mutsel(1'b1), .clk, .rst_n(!init), .cfg_mode(mode), .cfg_inv(inv), .cfg_t_on(t_on), .tx_step,
    .tx_half, .tx_bit, .line_set, .line_val, .tx_line(u[0]), .rx_sample, .rx_s, .rx_q1, .rx_q1_s,
    .rx_valid(u[1]), .rx_bit(u[2]), .rx_err(u[3]));
  always_comb assume (mode != 2'd3);
  always_comb if (!init) assert (r[0] == u[0] && r[1] == u[1] && r[3] == u[3] && (!r[1] || r[2] == u[2]));
endmodule
