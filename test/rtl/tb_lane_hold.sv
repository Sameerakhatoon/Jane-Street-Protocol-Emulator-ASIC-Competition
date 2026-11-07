// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps
// Response rule with RULE_CLK.hold, the SPI target first bit: a rule counts
// CS falling edges (pin-edge mode, tick delay 0, any position) and drives
// its response bit on txpin from the cycle after the edge is seen. With hold
// the bit stays until the program writes txpin (OUT, SET or MOV to PINS,
// side-set); the write and the end of the drive take effect at the same
// edge. A PINDIRS write or a PINS write that leaves txpin alone keeps the
// drive; stopping the lane ends it. Without hold the drive outlives the
// program's write until the next counted edge, as before.
module tb_lane_hold;
`include "lane_h.svh"

    localparam int MISO = 0, CS = 1, SCK = 2;

    task automatic pin_at(input int k, input int i, input logic v);   // vin[i] = v visible from cycle k
        while (cyc < k - 1) @(negedge clk);
        @(negedge clk) vin[i] = v;
    endtask

    logic vh [256];                               // vout[MISO] during each cycle
    logic eh [256];                               // voe[MISO]
    always @(posedge clk) if (run && cyc < 256) begin vh[cyc] <= vout[MISO]; eh[cyc] <= voe[MISO]; end

    function automatic logic [31:0] rule_clk(input bit hold);
        return (1 << tse_regs_pkg::LR_RULE_CLK_SRC_LO) | (CS << tse_regs_pkg::LR_RULE_CLK_CLK_PIN_LO)
             | (1 << tse_regs_pkg::LR_RULE_CLK_EDGE_LO) | (32'(hold) << tse_regs_pkg::LR_RULE_CLK_HOLD_LO);
    endfunction

    task automatic setup(input bit hold, input logic resp);
        cfg_set(tse_regs_pkg::LR_LANE_CFG, MISO << tse_regs_pkg::LR_LANE_CFG_TXPIN_LO);
        cfg_set(tse_regs_pkg::LR_RULE_CLK, rule_clk(hold));
        cfg_set(tse_regs_pkg::LR_RULE0_MATCH, 0);
        cfg_set(tse_regs_pkg::LR_RULE0_RESP_POS, (1 << tse_regs_pkg::LR_RULE0_RESP_POS_ANY_LO) | 32'(resp));
        cfg_set(tse_regs_pkg::LR_RULE0_CTL, (1 << tse_regs_pkg::LR_RULE0_CTL_DRIVE_LO) | (1 << tse_regs_pkg::LR_RULE0_CTL_EN_LO));
    endtask

    // MISO an output at m0, wait for CS low, then SCK high, then nb words b0, b1, then rel
    int p_rel, p_end;
    task automatic prog(input int nb, input logic [15:0] b0, b1, rel, input logic m0);
        pq.delete();
        emit(SET(tse_isa_pkg::SETDST_PINDIRS, 8'h11));
        emit(SET(tse_isa_pkg::SETDST_PINS, 8'h10 | 8'(m0)));
        emit(WAITI(0, tse_isa_pkg::WSRC_PIN, CS, 0, 0, 0));
        emit(WAITI(1, tse_isa_pkg::WSRC_PIN, SCK, 0, 0, 0));
        if (nb > 0) emit(b0);
        if (nb > 1) emit(b1);
        p_rel = pq.size(); emit(rel);
        p_end = pq.size(); emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q();
    endtask

    task automatic transaction();
        clear_disp(); vin = 4'b0010; start();
        pin_at(30, CS, 0);
        pin_at(50, SCK, 1);
        repeat (20) @(negedge clk);
    endtask

    function automatic bit held_until(input int a, input int b, input logic v);   // vh == v in cycles a to b
        for (int c = a; c <= b; c++) if (vh[c] !== v || eh[c] !== 1'b1) return 0;
        return 1;
    endfunction

    initial begin
        int w;
        repeat (3) @(negedge clk); rst_n = 1;
        repeat (2) @(negedge clk);

        // 1. hold, released by OUT PINS, 1 (OSR is 0 at RUN, so the program drives 0)
        setup(1, 1'b1);
        prog(0, 0, 0, OUT(tse_isa_pkg::OUTDST_PINS, 1, 0), 1'b0);
        transaction();
        w = int'(at(p_rel));
        check(vh[30] === 1'b0 && vh[31] === 1'b1, $sformatf("CS fall seen in cycle 30: MISO %b in 30, %b in 31 (rule bit from the next cycle)", vh[30], vh[31]));
        check(w > 50 && held_until(31, w, 1'b1), $sformatf("held: the rule bit stays through cycle %0d, the OUT's dispatch", w));
        check(vh[w + 1] === 1'b0 && vh[w + 5] === 1'b0 && !dut.drv_en, "the OUT ends the drive at its own edge: MISO shows the program's 0 from the next cycle");
        stop();

        // 2. hold: PINDIRS and a PINS write to another pin keep the drive; SET PINS on txpin releases
        setup(1, 1'b1);
        prog(2, SET(tse_isa_pkg::SETDST_PINDIRS, 8'h11), SET(tse_isa_pkg::SETDST_PINS, 8'h22),
             SET(tse_isa_pkg::SETDST_PINS, 8'h10), 1'b0);
        transaction();
        w = int'(at(p_rel));
        check(held_until(31, w, 1'b1) && vh[w + 1] === 1'b0,
              $sformatf("SET PINDIRS and SET PINS on pin 1 keep the drive; SET PINS on txpin in %0d releases it", w));
        stop();

        // 3. hold: MOV PINS releases; the response bit 0 is driven over the program's 1
        setup(1, 1'b0);
        prog(1, SET(tse_isa_pkg::SETDST_X, 1), 0, MOV(tse_isa_pkg::MOVDST_PINS, tse_isa_pkg::MOVSRC_X, 0), 1'b1);
        transaction();
        w = int'(at(p_rel));
        check(vh[30] === 1'b1 && held_until(31, w, 1'b0) && vh[w + 1] === 1'b1,
              $sformatf("a held 0 over the program's 1, released by MOV PINS in %0d", w));
        stop();

        // 4. hold: side-set on txpin releases at the first instruction that carries it
        setup(1, 1'b1);
        cfg_set(tse_regs_pkg::LR_LANE_CFG, (MISO << tse_regs_pkg::LR_LANE_CFG_TXPIN_LO) | (1 << tse_regs_pkg::LR_LANE_CFG_SIDE_EN_LO)
                | (2 << tse_regs_pkg::LR_LANE_CFG_SS_BASE_LO));
        prog(0, 0, 0, SS(DLY(0), 2'b00), 1'b0);
        transaction();
        check(dut.drv_en && vh[31] === 1'b1, "side-set on pins 2 and 3 does not release MISO (pin 0)");
        stop();
        cfg_set(tse_regs_pkg::LR_LANE_CFG, (MISO << tse_regs_pkg::LR_LANE_CFG_TXPIN_LO) | (1 << tse_regs_pkg::LR_LANE_CFG_SIDE_EN_LO)
                | (3 << tse_regs_pkg::LR_LANE_CFG_SS_BASE_LO));
        transaction();
        check(vh[31] === 1'b1 && !dut.drv_en, "side-set covering pin 0 (ss_base 3 wraps to 0) releases the drive at the next dispatch");
        stop();

        // 5. hold: stopping the lane ends the drive; the next run starts with MISO an input at 0
        setup(1, 1'b1);
        prog(0, 0, 0, OUT(tse_isa_pkg::OUTDST_PINS, 1, 0), 1'b0);
        clear_disp(); vin = 4'b0010; start();
        pin_at(30, CS, 0);
        repeat (5) @(negedge clk);
        check(dut.drv_en && vout[MISO] === 1'b1, "drive on before the stop");
        stop();
        check(!dut.drv_en, "stopping the lane ends a held drive");
        @(negedge clk) run = 1;
        #1 check(vout[MISO] === 1'b0 && voe[MISO] === 1'b0, "next run: MISO reads the cleared PINS and PINDIRS");
        stop();

        // 6. without hold the drive outlives the program's write (until the next counted edge)
        setup(0, 1'b1);
        prog(0, 0, 0, OUT(tse_isa_pkg::OUTDST_PINS, 1, 0), 1'b0);
        transaction();
        w = int'(at(p_rel));
        check(held_until(31, w + 10, 1'b1) && dut.drv_en, "hold 0: the rule keeps MISO after the OUT, as before");
        stop();

        // 7. a counted edge that does not match: hold keeps the bit, without hold the drive ends there
        //    (rule at position 0 only; CS falls in 30, rises in 38, falls again in 44)
        for (int h = 1; h >= 0; h--) begin
            setup(h, 1'b1);
            cfg_set(tse_regs_pkg::LR_RULE0_RESP_POS, 1);
            pq.delete();
            emit(SET(tse_isa_pkg::SETDST_CMD, 1 << tse_regs_pkg::LR_CMD_FRAME_RST_LO));
            emit(SET(tse_isa_pkg::SETDST_PINDIRS, 8'h11));
            emit(SET(tse_isa_pkg::SETDST_PINS, 8'h10));
            emit(WAITI(1, tse_isa_pkg::WSRC_PIN, SCK, 0, 0, 0));
            p_rel = pq.size(); emit(OUT(tse_isa_pkg::OUTDST_PINS, 1, 0));
            emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
            load_q();
            clear_disp(); vin = 4'b0010; start();
            pin_at(30, CS, 0);
            pin_at(38, CS, 1);
            pin_at(44, CS, 0);
            pin_at(50, SCK, 1);
            repeat (20) @(negedge clk);
            w = int'(at(p_rel));
            if (h) check(held_until(31, w, 1'b1) && vh[w + 1] === 1'b0, "hold 1: the second CS fall (no match) keeps the bit until the OUT");
            else   check(held_until(31, 44, 1'b1) && vh[45] === 1'b0, "hold 0: the drive ends after the second CS fall");
            stop();
        end
        finish("tb_lane_hold");
    end
endmodule
