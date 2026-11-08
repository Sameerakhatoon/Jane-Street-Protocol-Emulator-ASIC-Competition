// SPDX-License-Identifier: Apache-2.0
// A CAN transmitter and a host for the CAN in-bit ACK receiver's proof harness (top_node.sv), in
// simulation. rIC3 proves checkers 0, 3 and 7 alone, but their planted bugs need a trace through
// a frame's first words (several hundred cycles), too deep for the solver in an hour. This bench
// sends standard frames at 500 kbit/s (120 cycles a bit): random identifiers, RTR and lengths 0
// to 8, a correct CRC (now and then a corrupted one), stuffed as the bus stuffs them, then the
// delimiters, the ACK slot and EOF, and an idle gap. The bus is a wired AND of this transmitter
// and the node's txd (output 0), so the node's ACK shows on it; it reaches every input pin (the
// lane's RX pin is 1). The host always has a word ready (the role's `assume host_ready`). Each
// checker must be armed and answered; with LATE = k the checker k must fail.
`define CHK(k) \
  always @(posedge clk) if (dut.ob``k.armed && dut.ob``k.resp) resp[k]++;
module tb;
  logic clk = 0;
  logic tx = 1;
  int resp [8];
  wire node_dom = dut.voe[0] && !dut.vout[0];
  wire bus = tx && !node_dom;
  top #(.LATE(`LATE)) dut (.clk, .pin_in({4{bus}}), .h_tx_we(1'b1), .h_tx_data(32'd0), .h_rx_re(1'b1));
  always #5 clk = !clk;
  `CHK(0) `CHK(1) `CHK(2) `CHK(3) `CHK(4) `CHK(5) `CHK(6) `CHK(7)
  task automatic cycles(input int n);
    repeat (n) @(negedge clk);
  endtask
  task automatic send(input logic b);
    tx = b;
    cycles(120);
  endtask
  initial begin
    logic raw [$];
    logic [14:0] crc;
    logic [10:0] id;
    logic rtr, last;
    logic [3:0] dlc;
    int run;
    #1 dut.bank.qraw = dut.cfg_img;                 // the harness assumes this at reset
    cycles(200);
    for (int t = 0; t < `NTX; t++) begin
      id = $urandom; rtr = $urandom % 4 == 0; dlc = t < 9 ? t : $urandom % 9;
      raw.delete();
      raw.push_back(0);
      for (int i = 10; i >= 0; i--) raw.push_back(id[i]);
      raw.push_back(rtr); raw.push_back(0); raw.push_back(0);
      for (int i = 3; i >= 0; i--) raw.push_back(dlc[i]);
      if (!rtr) for (int k = 0; k < 8 * dlc; k++) raw.push_back(1'($urandom));
      crc = 0;
      foreach (raw[i]) begin
        logic fb = raw[i] ^ crc[14];
        crc = {crc[13:0], 1'b0};
        if (fb) crc ^= 15'h4599;
      end
      if ($urandom % 8 == 0) crc ^= 15'h0001;        // a corrupted CRC: no ACK this time
      for (int i = 14; i >= 0; i--) raw.push_back(crc[i]);
      run = 0;
      foreach (raw[i]) begin                        // stuff a bit after five equal ones
        send(raw[i]);
        run = i > 0 && raw[i] == last ? run + 1 : 1;
        last = raw[i];
        if (run == 5) begin send(!last); last = !last; run = 1; end
      end
      send(1);                                      // CRC delimiter
      send(1);                                      // ACK slot: recessive from us
      send(1);                                      // ACK delimiter
      repeat (7) send(1);                           // EOF
      cycles(3 * 120 + $urandom % 600);             // intermission and idle
    end
    for (int k = 0; k < 8; k++) $display("ob%0d answered %0d", k, resp[k]);
    $finish;
  end
endmodule
