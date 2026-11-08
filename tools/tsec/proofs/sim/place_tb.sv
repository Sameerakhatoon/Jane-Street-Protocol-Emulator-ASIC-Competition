// SPDX-License-Identifier: Apache-2.0
// A host and random pins for an own-instant harness (top_<role>_place.sv), in simulation.
// Nothing the place checks see depends on the outside world's timing (a wait for it ends a
// chain), so random pins and random host words drive every path the program takes. Each
// obligation must be checked (hit) at least once; with LATE = k obligation k must fail, and
// with LATE = -1 nothing may fail. PLACE_LOG prints each checked distance and its entry, to
// compare with the analysis' claims (<role>_place.txt). NG: the harness's obligation count.
// BANK: the harness has the configuration bank (a program with CSRW). HOSTMAX: every host word
// holds a command from 1 to HOSTMAX - 1 at bit HOSTSHIFT and random bits below, for a role that
// dispatches on a command field (a random word, or 0, which the SPI and I2C controllers give no
// exit, leaves it in a state it never leaves). PROBE: the harness has a chain (p_dly); LEMLOG
// then prints q per cycle for place_lemmas.py. RXRE: the host reads received data in one cycle
// in RXRE at random, not in every cycle (the solver's host may wait as long as it likes).
module tb;
  logic clk = 0;
  logic [3:0] pin_in = 0;
  logic h_tx_we = 0, h_rx_re = 1;
  logic [31:0] h_tx_data = 0;
  int hits [`NG];
  top #(.LATE(`LATE)) dut (.clk, .pin_in, .h_tx_we, .h_tx_data, .h_rx_re);
  localparam int TW = $bits(dut.gv) / `NG;
  always #5 clk = !clk;
  always @(posedge clk)
    for (int g = 0; g < `NG; g++)
      if (dut.hit[g]) begin
        hits[g]++;
`ifdef PLACE_LOG
        $display("place g%0d k%0d %0d", g, dut.gk[g * 8 +: 8], dut.gv[g * TW +: TW]);
`endif
      end
`ifdef PROBE
  // a chain's harness: p_dly is the sequencer's delay count, as the sby script ties it
  initial force dut.p_dly = dut.dut.u_seq.dly;
`ifdef LEMLOG
  // what place_lemmas.py proposes lemmas from: q at each chain, entry and pc
  always @(posedge clk)
    if (dut.pch != 0) $display("lem %0d %0d %0d %0d %0d", dut.pch, dut.pk, dut.pc, dut.q, dut.pc_q);
`endif
`endif
  initial begin
`ifdef BANK
    #1 dut.bank.qraw = dut.cfg_img;              // the harness assumes this at reset
`endif
    for (int c = 0; c < `NCYC; c++) begin
      @(negedge clk);
      h_tx_we = $urandom % 4 == 0;
`ifdef RXRE
      h_rx_re = $urandom % `RXRE == 0;
`endif
`ifdef HOSTMAX
      h_tx_data = (1 + $urandom % (`HOSTMAX - 1)) << `HOSTSHIFT | $urandom % (1 << `HOSTSHIFT);
`else
      h_tx_data = $urandom;
`endif
      if ($urandom % `PINHOLD == 0) pin_in = $urandom;
    end
    for (int g = 0; g < `NG; g++) $display("ob%0d answered %0d", g, hits[g]);
    $finish;
  end
endmodule
