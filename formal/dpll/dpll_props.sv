// SPDX-License-Identifier: Apache-2.0
// Formal harness for tse_dpll, immediate assertions only. For every legal
// configuration (2 to 31 cycles per bit in sixteenths, sample point inside
// the bit, SJW at most a quarter bit, any edge options) and every input:
//  - q1, half and sample each fire at most once between two bit starts;
//  - q1 and sample never fire while the DPLL waits for a hard sync;
//  - with no edge since the last bit start (and none in the two cycles
//    before it, while a correction is still being applied), the next bit
//    starts between floor and ceil of period / 16 cycles later.
module dpll_props (
  input logic clk,
  input logic enable,
  input logic hard_arm,
  input logic s_p,
  input logic s_n
);
  (* anyconst *) logic [19:0] period, spt;
  (* anyconst *) logic [15:0] sjw;
  (* anyconst *) logic        resync, dual, rise, fall;
  always_comb assume (period >= 20'd32 && period < 20'd496 && spt < period
                      && {4'd0, sjw} <= (period >> 2));

  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;

  logic bs, half, q1, sample, sv, edge_seen;
  tse_dpll dut (
    .clk, .rst_n(!init), .cfg_period(period), .cfg_spt(spt), .cfg_sjw(sjw), .cfg_resync(resync),
    .cfg_dual(dual), .cfg_rise(rise), .cfg_fall(fall), .enable(enable && !init), .hard_arm,
    .s_p, .s_n, .bit_start(bs), .half, .q1, .sample, .samp_val(sv), .edge_seen
  );

  // harness bookkeeping, reset by every bit start
  logic       seen_q1, seen_half, seen_sample, armed, quiet, e1, e2;
  logic [5:0] since;
  always_ff @(posedge clk)
    if (init) begin
      seen_q1 <= 1'b0; seen_half <= 1'b0; seen_sample <= 1'b0; armed <= 1'b0;
      quiet <= 1'b0; since <= 6'd0; e1 <= 1'b0; e2 <= 1'b0;
    end else begin
      e1 <= edge_seen; e2 <= e1;
      if (hard_arm) armed <= 1'b1;
      if (edge_seen && armed) armed <= 1'b0;
      if (!enable) begin
        quiet <= 1'b0; seen_q1 <= 1'b0; seen_half <= 1'b0; seen_sample <= 1'b0;
      end else if (bs) begin
        seen_q1 <= 1'b0; seen_half <= 1'b0; seen_sample <= 1'b0;
        since <= 6'd1; quiet <= !edge_seen && !e1 && !e2;
      end else begin
        seen_q1 <= seen_q1 || q1; seen_half <= seen_half || half; seen_sample <= seen_sample || sample;
        if (since != 6'd63) since <= since + 6'd1;
        if (edge_seen) quiet <= 1'b0;
      end
    end

  wire [5:0] lo = 6'(period >> 4);
  wire [5:0] hi = 6'((period + 20'd15) >> 4);

  always_comb if (!init && enable) begin
    if (seen_q1) assert (!q1);
    if (seen_half) assert (!half);
    if (seen_sample) assert (!sample);
    if (armed) assert (!q1 && !sample);
    if (quiet && !edge_seen) begin
      assert (since <= hi);
      if (bs) assert (since >= lo);
    end
  end
  wire _unused = &{sv, 1'b0};
endmodule
