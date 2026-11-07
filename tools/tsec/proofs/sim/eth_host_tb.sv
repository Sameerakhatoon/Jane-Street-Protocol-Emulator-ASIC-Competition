// SPDX-License-Identifier: Apache-2.0
// A host and random pins for the 10BASE-T transmitter proof harness (top_mac_tx.sv), in
// simulation. rIC3 proves ob1 to ob3; the planted bugs (late<k>) did not fail within an hour,
// so this bench shows each checker armed and answered, and with LATE = k checker k must fail.
// HOST = 1 sends random frames (ob1 to ob4); the link pulse (ob0) needs a silent host for a
// whole timer period, so it runs with HOST = 0 and a longer NCYC.
`define CHK(k) \
  always @(posedge clk) if (dut.ob``k.armed && dut.ob``k.resp) resp[k]++;
module tb;
  logic clk = 0;
  logic [3:0] pin_in = 0;
  logic h_tx_we = 0;
  logic [31:0] h_tx_data = 0;
  int resp [5];
  top #(.LATE(`LATE)) dut (.clk, .pin_in, .h_tx_we, .h_tx_data, .h_rx_re(1'b1));
  always #5 clk = !clk;
  `CHK(0) `CHK(1) `CHK(2) `CHK(3) `CHK(4)
  initial begin
    #1 dut.bank.qraw = dut.cfg_img;              // the harness assumes this at reset
    for (int c = 0; c < `NCYC; c++) begin
      @(negedge clk);
      h_tx_we = `HOST && $urandom % 4 == 0;
      h_tx_data = $urandom & 32'h003F_00FF;
      pin_in = $urandom;
    end
    for (int k = 0; k < 5; k++) $display("ob%0d answered %0d", k, resp[k]);
    $finish;
  end
endmodule
