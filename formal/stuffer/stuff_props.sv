// SPDX-License-Identifier: Apache-2.0
// Formal harness for tse_stuffer, immediate assertions only. The TX side
// feeds the RX side of the same instance: rx_step = tx_step, rx_din = tx_dout
// (inverted when the harness corrupts a stuff bit). For every run length n
// 1..15, every mode and every stream of bits, steps, clears and corruptions:
//  - exactly the data bits come out of the destuffer, unchanged, one cycle
//    after they were taken;
//  - the destuffer flags a stuff error exactly in the cycle after a corrupted
//    stuff bit, and never on its own stuffer's line;
//  - an independent line monitor never sees a run of a counted polarity
//    longer than n (CAN, n = 5: never six equal bits).
// After a corruption the two sides disagree on the line history, so only
// the error flag of the next cycle is checked, and no further corruption is
// made, until the next clear.
module stuff_props (
  input logic clk,
  input logic clear_in,
  input logic step,
  input logic din,
  input logic corrupt
);
  (* anyconst *) logic [3:0] n;
  (* anyconst *) logic [1:0] mode;
  always_comb assume (n != 4'd0 && mode != 2'd0);

  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;
  wire clear = !init && clear_in;

  logic take, line, pend, rv, rb, rerr;
  logic want_v, want_b, want_e, spoiled;
  wire  flip = !init && !clear && !spoiled && step && pend && corrupt;
  tse_stuffer dut (
    .clk, .rst_n(!init), .cfg_n(n), .cfg_mode(mode), .clear,
    .tx_step(step), .tx_din(din), .tx_take(take), .tx_dout(line), .tx_pending(pend),
    .rx_step(step), .rx_din(line ^ flip), .rx_valid(rv), .rx_dout(rb), .rx_err(rerr)
  );

  // what the destuffer must deliver in the next cycle
  always_ff @(posedge clk) begin
    want_v  <= !init && !clear && take;
    want_b  <= din;
    want_e  <= flip;
    spoiled <= !init && !clear && (spoiled || flip);
  end

  // the line monitor: length of the current run of equal line bits
  logic [4:0] run;
  logic       rlast;
  always_ff @(posedge clk)
    if (init || clear) begin
      run <= 5'd0; rlast <= 1'b0;
    end else if (step) begin
      run   <= (run != 5'd0 && line == rlast) ? run + 5'd1 : 5'd1;
      rlast <= line;
    end
  wire counted = (mode[0] && rlast) || (mode[1] && !rlast);

  always_comb if (!init) begin
    if (!spoiled || want_e) assert (rerr == want_e);
    if (!spoiled) begin
      assert (rv == want_v);
      if (rv) assert (rb == want_b);
    end
    if (counted) assert (run <= {1'b0, n});
  end
endmodule
