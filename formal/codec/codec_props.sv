// SPDX-License-Identifier: Apache-2.0
// Formal harness for tse_codec, immediate assertions only. The encoder's
// line feeds the decoder of the same instance with ideal strobes: a free
// input adv moves a bit through its phases, any number of idle cycles apart.
//   NRZ, NRZI:   tx_step (bit taken) -> rx_sample (line sampled)
//   Manchester:  tx_step -> rx_q1 (first half sampled) -> tx_half -> rx_sample
// For every mode, polarity and NRZI sense and every bit stream:
//  - each decoded bit equals the bit sent;
//  - Manchester flags a code violation exactly when the harness inverts the
//    first-half sample (both halves then read equal), the other codes never;
//  - the line itself follows the code, checked apart from the decoder (a
//    mistake shared by encoder and decoder would survive the round trip):
//    NRZ b ^ inv; NRZI toggles when b equals t_on; Manchester ~b ^ inv,
//    then b ^ inv after the half strobe;
//  - tx_half strobes outside Manchester leave the line alone;
//  - line_set forces the line to line_val in the next cycle (NRZ and
//    Manchester, between bits; in NRZI it would change the decoder's
//    reference level, so it is not used there).
module codec_props (
  input logic clk,
  input logic adv,
  input logic din,
  input logic bad,
  input logic half_x,
  input logic force_in,
  input logic force_val
);
  (* anyconst *) logic [1:0] mode;
  (* anyconst *) logic       inv, t_on;
  always_comb assume (mode != 2'd3);
  wire man = mode == 2'd2;

  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;

  logic [1:0] ph;                      // 0 send, 1 sample (q1 if Manchester), 2 half, 3 sample
  logic       sent, viol;
  wire go     = !init && adv;
  wire step   = go && ph == 2'd0;
  wire q1     = go && man && ph == 2'd1;
  wire half   = (go && man && ph == 2'd2) || (!init && !man && half_x);
  wire sample = go && (man ? ph == 2'd3 : ph == 2'd1);
  wire frc    = !init && force_in && !go && ph == 2'd0 && mode != 2'd1;
  always_ff @(posedge clk)
    if (init) ph <= 2'd0;
    else if (go) ph <= (sample) ? 2'd0 : ph + 2'd1;
  always_ff @(posedge clk) begin
    if (step) sent <= din;
    if (q1) viol <= bad;
  end

  logic line, rv, rb, rerr;
  tse_codec dut (
    .clk, .rst_n(!init), .cfg_mode(mode), .cfg_inv(inv), .cfg_t_on(t_on),
    .tx_step(step), .tx_half(half), .tx_bit(din), .line_set(frc), .line_val(force_val),
    .tx_line(line), .rx_sample(sample), .rx_s(line), .rx_q1(q1), .rx_q1_s(line ^ bad),
    .rx_valid(rv), .rx_bit(rb), .rx_err(rerr)
  );

  logic want, want_e, forced, fval, stepped, halved, lprev;
  always_ff @(posedge clk) begin
    stepped <= step;
    halved  <= go && man && ph == 2'd2;
    lprev   <= line;
    if (sample) begin want <= sent; want_e <= man && viol; end
    forced <= frc;
    fval   <= force_val;
  end

  always_comb if (!init) begin
    if (rv) begin
      assert (rb == want);
      assert (rerr == want_e);
    end else assert (!rerr);
    if (forced) assert (line == fval);
    if (stepped) begin
      if (mode == 2'd0) assert (line == (sent ^ inv));
      if (mode == 2'd1) assert (line == (lprev ^ (sent == t_on)));
      if (man) assert (line == (!sent ^ inv));
    end
    if (halved) assert (line == (sent ^ inv));
  end
endmodule
