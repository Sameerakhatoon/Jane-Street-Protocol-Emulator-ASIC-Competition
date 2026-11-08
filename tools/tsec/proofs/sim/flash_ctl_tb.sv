// SPDX-License-Identifier: Apache-2.0
// A mode 0 SPI controller and a host for the SPI flash target proof harness (top_flash.sv), in
// simulation. rIC3 proves each window check alone, and finds the planted bugs of the first
// three, but the later ones need a trace through an opcode and an address (some 250 to 450
// cycles), too deep for the solver in an hour. This bench drives legal frames (each of the six
// commands and an unknown opcode, random addresses and lengths, SCK half periods of 60 cycles or
// more, CS setup 200 ns or more) and shows each checker armed and answered; with LATE = k the
// checker k must fail. Input pins: mosi 0, cs_n 1, sck 2 (miso is output 0). The host always has
// a word ready (the role's `assume host_ready`).
`define CHK(k) \
  always @(posedge clk) if (dut.ob``k.armed && dut.ob``k.resp) resp[k]++;
module tb;
  logic clk = 0;
  logic cs_n = 1, sck = 0, mosi = 0;
  logic [31:0] h_tx_data = 0;
  int resp [10];
  top #(.LATE(`LATE)) dut (.clk, .pin_in({1'b0, sck, cs_n, mosi}), .h_tx_we(1'b1), .h_tx_data,
    .h_rx_re(1'b1));
  always #5 clk = !clk;
  `CHK(0) `CHK(1) `CHK(2) `CHK(3) `CHK(4) `CHK(5) `CHK(6) `CHK(7) `CHK(8) `CHK(9)
  always @(negedge clk) h_tx_data = $urandom;
  task automatic cycles(input int n);
    repeat (n) @(negedge clk);
  endtask
  task automatic bit_(input logic d);            // SCK low on entry and return
    cycles(1 + $urandom % 3);                     // t_mo: MOSI changes just after the fall
    mosi = d;
    cycles(62 + $urandom % 30);                   // a half period, past the checkers' QUIET
    sck = 1;
    cycles(64 + $urandom % 30);
    sck = 0;
    cycles(3);
  endtask
  task automatic bits(input int n, input logic [31:0] v);
    for (int i = n - 1; i >= 0; i--) bit_(v[i % 32]);
  endtask
  initial begin
    // a read leaves the lane in the next word's loop until SCK clocks it out (the spec's note on
    // frame ends): it would swallow the next frames, so the read comes last
    logic [7:0] ops [7] = '{8'h9F, 8'h05, 8'h06, 8'h02, 8'h20, 8'hA5, 8'h03};
    logic [7:0] op;
    cycles(100);
    for (int t = 0; t < `NTX; t++) begin
      op = t == `NTX - 1 ? 8'h03 : ops[t < 6 ? t : $urandom % 6];
      cs_n = 0;
      cycles(12 + $urandom % 30);                 // t_css 200 ns is 12 cycles
      bits(8, op);
      case (op)
        8'h9F: bits(24, $urandom);
        8'h05: bits(8 * (1 + $urandom % 3), $urandom);
        8'h03: begin bits(24, $urandom); bits(32 * (1 + $urandom % 2), $urandom); end
        8'h02: begin bits(24, $urandom); bits(8 * (1 + $urandom % 3), $urandom); end
        8'h20: bits(24, $urandom);
        default: bits($urandom % 9, $urandom);
      endcase
      cycles(20 + $urandom % 40);
      cs_n = 1;
      cycles(150 + $urandom % 100);
    end
    for (int k = 0; k < 10; k++) $display("ob%0d answered %0d", k, resp[k]);
    $finish;
  end
endmodule
