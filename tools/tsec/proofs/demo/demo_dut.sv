// SPDX-License-Identifier: Apache-2.0
// Stand-in for a lane answering an edge: 2-flop synchronizer, WAIT retires in
// the cycle the synchronized value changes, the reacting instruction issues one
// cycle later (D = 1), and the output flop shows it one cycle after that.
module demo_dut (
  input  logic clk,
  input  logic rst,
  input  logic pin_in,
  output logic pin_out
);
  logic s1, s2, s2_q, react;
  always_ff @(posedge clk) begin
    if (rst) begin
      {s1, s2, s2_q, react, pin_out} <= '0;
    end else begin
      s1   <= pin_in;
      s2   <= s1;
      s2_q <= s2;
`ifdef BUG_EXTRA_STAGE
      react <= s2_q && !react;
`else
      react <= s2 && !s2_q;
`endif
      if (react) pin_out <= 1'b1;
      if (!s2) pin_out <= 1'b0;
    end
  end
endmodule

module top (
  input logic clk,
  input logic pin_in
);
  logic init = 1'b1;
  always_ff @(posedge clk) init <= 1'b0;
  logic pin_out;
  demo_dut dut (.clk, .rst(init), .pin_in, .pin_out);
  tse_window_check #(.LO(`LO), .HI(`HI), .QUIET(6)) chk (
    .clk, .rst(init), .en(1'b1), .trig_in(pin_in), .trig_val(1'b1), .resp_out(pin_out), .resp_val(1'b1),
    .abandon(1'b0));
endmodule
