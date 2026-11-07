// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps
// Pin-level timing of the chip side through the Tiny Tapeout pins:
//   LANE_IN sample delay in half cycles (1, 2 and 3, one inverted) for an input
//   change in the first and in the second half of a cycle; a lane input fed
//   from the chip's own uo_out value;
//   a hold response rule clocked by a CS fall: uo_out changes 4 cycles after the
//   cycle of the pin change (edge seen in k + 2, port at the edge ending k + 3),
//   and keeps the bit until the program's OUT;
//   EMIT to the trigger pad in 3 edges, the TRIG flag visible to WAIT from
//   EMIT + 2, and ending a WAIT; event records carry the cycle of their EMIT; TICK every 16
//   cycles (EV_CTL.tick_k = 4). Cycle n runs from rising edge n; signals are
//   sampled at the falling edge.
module tb_chip_pins;
    import tse_regs_pkg::*;

`include "chip_h.svh"

    int cyc = 0;                                  // cycle n runs from its rising edge to the next
    always @(posedge clk) cyc <= cyc + 1;

    function automatic logic [15:0] CSRR(input int a); return tse_isa_pkg::CSRR_MATCH | 16'(a); endfunction
    function automatic logic [15:0] EMIT(input int c); return tse_isa_pkg::EMIT_MATCH | 16'(c << tse_isa_pkg::EMIT_CODE_LO); endfunction
    localparam logic [15:0] OUT1 = tse_isa_pkg::OUT_MATCH;          // OUT PINS, 1
    localparam logic [15:0] DLY0 = tse_isa_pkg::DLY_MATCH;
    localparam int LANE1 = LANE_HOST_BASE + LANE_HOST_STRIDE;

    // first cycle (k + 0 .. k + n) in which sig shows v
    // watched signals: lane input vpins 0, 2, 3 and 7, uo_out[0], [1], [7], lane 0 event, TRIG flag
    localparam int V0 = 0, V2 = 1, V3 = 2, V7 = 3, O0 = 4, O1 = 5, O7 = 6, EV0 = 7, TRG = 8;
    logic [8:0] sig;
    assign sig = {dut.u_top.chip_flags[4], dut.u_top.ev_valid[0], uo_out[7], uo_out[1], uo_out[0],
                  dut.u_top.vin_p[7], dut.u_top.vin_p[3], dut.u_top.vin_p[2], dut.u_top.vin_p[0]};

    task automatic first(input int k, input int n, input int id, input logic v, output int at);
        at = -1;
        while (cyc < k + n) begin
            @(negedge clk);                       // values of cycle cyc, settled
            if (at < 0 && sig[id] === v) at = cyc;
        end
    endtask

    initial begin
        logic [15:0] p0 [12], p1 [6];
        logic [31:0] w [], rd, r7, r8, rt;
        logic [7:0]  s;
        int k, a2, a3, a7, a0, k1, k7, k0, m;
        errors = 0;
        ui_in = 8'h00; uio_in = 8'h00; rst_n = 1'b1;
        repeat (4) @(negedge clk);
        rst_n = 1'b0;
        repeat (8) @(negedge clk);
        ld(8'h10, 1);                                       // BEGIN
        crc = 32'hFFFFFFFF;

        p0 = '{SET(1, 8'h11), SET(0, 8'h10), WAITI(1, 0, 1, 0, 0, 0), DLY0,
               CSRR(LR_TS), EMIT(7), WAITI(1, 3, tse_isa_pkg::WIDX_CHIP_TRIG, 0, 0, 0), SET(0, 8'h11),
               WAITI(1, 3, tse_isa_pkg::WIDX_CHIP_TICK, 0, 0, 0), CSRR(LR_TS), EMIT(8), JMP(0, 11)};
        w = new[6];
        foreach (w[i]) w[i] = {p0[2*i + 1], p0[2*i]};
        ld_wrb(16'h0000, w);
        p1 = '{SET(1, 8'h11), SET(0, 8'h10), WAITI(0, 0, 1, 0, 0, 0), WAITI(1, 0, 2, 0, 0, 0), OUT1, JMP(0, 5)};
        w = new[3];
        foreach (w[i]) w[i] = {p1[2*i + 1], p1[2*i]};
        ld_wrb(16'h0080, w);

        ld_wr(LANE1 + 16'(LR_LANE_PROG), 32'd1 << LR_LANE_PROG_WINDOW_LO);
        ld_wr(LANE1 + 16'(LR_RULE_CLK), (1 << LR_RULE_CLK_SRC_LO) | (1 << LR_RULE_CLK_CLK_PIN_LO)
                                        | (1 << LR_RULE_CLK_EDGE_LO) | (1 << LR_RULE_CLK_HOLD_LO));
        ld_wr(LANE1 + 16'(LR_RULE0_RESP_POS), (1 << LR_RULE0_RESP_POS_ANY_LO) | 1);
        ld_wr(LANE1 + 16'(LR_RULE0_CTL), (1 << LR_RULE0_CTL_DRIVE_LO) | (1 << LR_RULE0_CTL_EN_LO));
        ld_wr(HR_PAD_OUT + 0, 32'h40);                      // uo_out[0] <- lane 0 vpin 0
        ld_wr(HR_PAD_OUT + 1, 32'h44);                      // uo_out[1] <- lane 1 vpin 0
        ld_wr(HR_PIN_OPT, 32'd1 << HR_PIN_OPT_TRIG_PAD_LO);
        ld_wr(HR_LANE_IN + 0, 32'd17);                      // lane 0 vpin 0 <- uo_out[1]
        ld_wr(HR_LANE_IN + 1, 32'd4);                       // lane 0 vpin 1 <- ui_in[4] (start)
        ld_wr(HR_LANE_IN + 2, 32'd6 | (1 << HR_LANE_IN_DELAY_LO));
        ld_wr(HR_LANE_IN + 3, 32'd6 | (2 << HR_LANE_IN_DELAY_LO));
        ld_wr(HR_LANE_IN + 5, 32'd5);                       // lane 1 CS <- ui_in[5]
        ld_wr(HR_LANE_IN + 6, 32'd7);                       // lane 1 SCK <- ui_in[7]
        ld_wr(HR_LANE_IN + 7, 32'd6 | (1 << HR_LANE_IN_INV_LO) | (3 << HR_LANE_IN_DELAY_LO));
        ld_wr(HR_EV_CTL, 32'h1 | (32'h1 << HR_EV_CTL_LANE_MASK_LO) | (32'd4 << HR_EV_CTL_TICK_K_LO));
        ld_wr(HR_TRIG_MATCH, 32'h1 | (32'd7 << HR_TRIG_MATCH_CODE_LO) | (32'h3F << HR_TRIG_MATCH_CODE_MASK_LO));
        ld_wr(HR_TRIG_CTL, (32'd1 << HR_TRIG_CTL_EN_LO) | (32'd1 << HR_TRIG_CTL_POL_LO));
        ld(8'h12, 1);                                       // COMMIT
        for (int i = 3; i >= 0; i--) ld(8'(~crc >> (8 * i)), 0);
        ld_stat(s);
        check("STAT after COMMIT, loaded", 32'(s[0]), 32'd1);

        ui_in = 8'b0010_0001;                               // host CS_N high, lane 1 CS high
        @(negedge clk) rst_n = 1'b1;
        repeat (20) @(negedge clk);
        spi_wr(HR_RUN, 32'h3);
        repeat (100) @(negedge clk);

        // sample delay: a change in the second half of cycle k (at the falling edge)
        k = cyc; ui_in[6] = 1'b1;
        fork
            first(k, 8, V2, 1'b1, a2);
            first(k, 8, V3, 1'b1, a3);
            first(k, 8, V7, 1'b0, a7);
        join
        check("second half: delay 1 sees the change in k + 3", 32'(a2 - k), 32'd3);
        check("second half: delay 2 in k + 3", 32'(a3 - k), 32'd3);
        check("second half: delay 3, inverted, in k + 4", 32'(a7 - k), 32'd4);
        repeat (10) @(negedge clk);
        ui_in[6] = 1'b0;
        repeat (10) @(negedge clk);
        @(posedge clk); #2;                                 // a change early in cycle k
        k = cyc; ui_in[6] = 1'b1;
        fork
            first(k, 8, V2, 1'b1, a2);
            first(k, 8, V3, 1'b1, a3);
            first(k, 8, V7, 1'b0, a7);
        join
        check("first half: delay 1 sees the change in k + 2", 32'(a2 - k), 32'd2);
        check("first half: delay 2 in k + 3", 32'(a3 - k), 32'd3);
        check("first half: delay 3, inverted, in k + 3", 32'(a7 - k), 32'd3);

        // hold rule on lane 1: CS falls in cycle k, the response bit 1 leaves on uo_out[1]
        @(negedge clk) k = cyc; ui_in[5] = 1'b0;
        fork
            first(k, 10, O1, 1'b1, a0);
            first(k, 12, V0, 1'b1, m);
        join
        check("CS fall in k: uo_out[1] shows the rule bit from k + 4", 32'(a0 - k), 32'd4);
        check("lane input from the chip's own uo_out[1]: 2 cycles later", 32'(m - a0), 32'd2);
        repeat (30) @(negedge clk);
        check("the bit holds while the program waits for SCK", 32'(uo_out[1]), 32'd1);
        ui_in[7] = 1'b1;
        repeat (12) @(negedge clk);
        check("the OUT after SCK hands MISO to the program (0)", 32'(uo_out[1]), 32'd0);

        // EMIT 7 in cycle c (ev_valid in c + 1), trigger pad in c + 3, TRIG ends the WAIT: uo_out[0] in c + 5
        @(negedge clk) k = cyc; ui_in[4] = 1'b1;
        fork
            first(k, 40, EV0, 1'b1, k1);
            first(k, 40, O7, 1'b1, k7);
            first(k, 40, O0, 1'b1, k0);
            first(k, 40, TRG, 1'b1, m);
        join
        check("EMIT to trigger pad: 2 cycles after the event reaches the unit", 32'(k7 - k1), 32'd2);
        check("TRIG flag visible to WAIT one cycle after the event reaches the unit (EMIT + 2)", 32'(m - k1), 32'd1);
        check("WAIT CHIP TRIG ended: uo_out[0] rose", 32'(k0 > k7), 32'd1);
        repeat (100) @(negedge clk);

        // the log: TIME, then the TRIG record, the EMIT 7 and EMIT 8 records
        r7 = 0; r8 = 0; rt = 0;
        for (int i = 0; i < 4; i++) begin
            spi_rd(HR_EV_POP, rd);
            if (rd[29:24] == 6'h07) r7 = rd;
            if (rd[29:24] == 6'h08) r8 = rd;
            if (rd[29:24] == 6'h3E) rt = rd;
        end
        check("EMIT after CSRR TS: record timestamp = TS read + 1 (the EMIT's own cycle)", 32'(r7[7:0] - r7[23:16]), 32'd1);
        check("TRIG record carries the cycle of the EMIT that fired it", 32'(rt[15:0]), 32'(r7[15:0]));
        check("WAIT TICK, then CSRR TS: TS mod 16 = 1", 32'(r8[19:16]), 32'd1);

        if (errors == 0) $display("PASS tb_chip_pins");
        else             $display("FAIL tb_chip_pins errors=%0d", errors);
        $finish;
    end
endmodule
