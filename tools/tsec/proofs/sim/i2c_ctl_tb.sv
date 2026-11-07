// SPDX-License-Identifier: Apache-2.0
// An I2C controller and a host for the I2C target proof harness (top_tgt.sv), in simulation.
// The rIC3 runs prove each window check, but a planted bug there needs a trace deeper than
// an address byte, too deep for the solver. This bench drives legal Fast-mode transactions
// (random addresses, half to 0x50, reads and writes, clock stretching, repeated START) and
// shows each checker armed and answered; with LATE = k the checker k must fail.
`define CHK(k) \
  always @(posedge clk) if (dut.ob``k.armed && dut.ob``k.resp) resp[k]++;
module tb;
  logic clk = 0;
  logic m_scl = 1, m_sda = 1;
  logic h_tx_we = 0;
  logic [31:0] h_tx_data = 0;
  int resp [10];
  wire t_sda = !(dut.voe[0] && !dut.vout[0]);   // open drain: low when either side pulls
  wire t_scl = !(dut.voe[1] && !dut.vout[1]);
  top #(.LATE(`LATE)) dut (.clk, .pin_in({2'b11, m_scl && t_scl, m_sda && t_sda}),
    .h_tx_we, .h_tx_data, .h_rx_re(1'b1));
  always #5 clk = !clk;
  `CHK(0) `CHK(1) `CHK(2) `CHK(3) `CHK(4) `CHK(5) `CHK(6) `CHK(7) `CHK(8) `CHK(9)
  always @(negedge clk) begin                   // the host answers at a random delay
    h_tx_we = $urandom % 8 == 0;
    h_tx_data = $urandom % 4 == 0 ? 32'h1 : {24'd0, 8'($urandom)};
  end
  task automatic cycles(input int n);
    repeat (n) @(negedge clk);
  endtask
  task automatic scl_high();                     // release SCL and wait out any stretching
    m_scl = 1;
    while (!t_scl) @(negedge clk);
  endtask
  task automatic pulse(input logic d, output logic seen);   // SCL low on entry and return
    cycles(8 + $urandom % 20);                   // t_hd_dat; tSU;DAT is 6 cycles or more
    m_sda = d;
    cycles(80 + $urandom % 60);                  // t_low 1.3 us is 78 cycles
    scl_high();
    cycles(40 + $urandom % 30);                  // t_high 0.6 us is 36 cycles
    seen = m_sda && t_sda;
    m_scl = 0;
  endtask
  initial begin
    logic [7:0] a, b;
    logic s;
    // the target's program has no CSRW, so its harness holds the bank as the image (a constant):
    // nothing to load here, unlike the transmitter bench
    cycles(50);
    for (int t = 0; t < `NTX; t++) begin
      m_sda = 1; cycles(10); scl_high(); cycles(40 + $urandom % 20);
      m_sda = 0; cycles(40 + $urandom % 20);     // START, then tHD;STA
      m_scl = 0;
      a = $urandom % 2 ? {7'h50, 1'($urandom)} : 8'($urandom);
      for (int i = 7; i >= 0; i--) pulse(a[i], s);
      pulse(1, s);
      for (int k = 0; k < 1 + $urandom % 3 && !s; k++) begin
        b = $urandom;
        for (int i = 7; i >= 0; i--) pulse(a[0] ? 1'b1 : b[i], s);
        pulse(a[0] ? 1'($urandom % 3 == 0) : 1'b1, s);   // a read ends on a NACK
      end
      if ($urandom % 4 == 0) begin
        m_sda = 1; cycles(30);                    // a repeated START comes next
      end else begin
        cycles(10); m_sda = 0; cycles(80); scl_high();
        cycles(40 + $urandom % 20); m_sda = 1;    // STOP, then tBUF
        cycles(90 + $urandom % 40);
      end
    end
    for (int k = 0; k < 10; k++) $display("ob%0d answered %0d", k, resp[k]);
    $finish;
  end
endmodule
