`timescale 1ns/1ps
// Clock C and guards: C counts cycles from RUN; TMR cclr makes C read 0 in the
// next cycle; "TMR cclr, gsel=1 in cycle c, then WAIT 1, FLAG, GUARD" ends the
// wait in exactly c + 1 + GT1; JMP GUARD falls through before GT0 and jumps
// after; CSRR CLOCK and CSRR TS read C and the chip timestamp; WAIT CHIP sees
// the chip flags and its reserved indices 6 and 7 read 0; FLAG SYNCED is 0
// while the DPLL waits for a hard sync.
module tb_lane_clock;
`include "lane_h.svh"

    localparam int GT0 = 5, GT1 = 40;
    function automatic logic [15:0] TMR(input int cclr, gwe, gsel, d);
        return tse_isa_pkg::TMR_MATCH | f(cclr, tse_isa_pkg::TMR_CCLR_LO) | f(gwe, tse_isa_pkg::TMR_GWE_LO)
             | f(gsel, tse_isa_pkg::TMR_GSEL_LO) | f(d, tse_isa_pkg::TMR_DELAY_LO);
    endfunction

    // timestamp and X, Y at the cycle after each dispatch of pc ts_pc
    int ts_pc = -1;
    logic [31:0] ts_seen, yx_after;
    always @(posedge clk) if (run && dut.u_seq.avail && !dut.u_seq.held && dut.u_seq.pc == ts_pc) ts_seen <= ts;

    function automatic logic [31:0] yx(); return {dut.u_seq.y, dut.u_seq.x}; endfunction

    initial begin
        int p_mov, p_tmr, p_mov2, p_g, p_j, p_rd, p_ts, p_w, p_s;
        logic [31:0] v;
        repeat (3) @(negedge clk); rst_n = 1;
        repeat (2) @(negedge clk);
        cfg_set(tse_regs_pkg::LR_GUARD01, (GT1 << tse_regs_pkg::LR_GUARD01_GT1_LO) | GT0);

        // 1. MOV X, C reads the cycle number; TMR cclr [3] then MOV X, C reads 3
        pq.delete();
        emit(DLY(7));
        p_mov = pq.size(); emit(MOV(tse_isa_pkg::MOVDST_X, tse_isa_pkg::MOVSRC_C, 0));
        p_tmr = pq.size(); emit(TMR(1, 0, 0, 3));
        p_mov2 = pq.size(); emit(MOV(tse_isa_pkg::MOVDST_Y, tse_isa_pkg::MOVSRC_C, 0));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); clear_disp(); start(); repeat (40) @(negedge clk); stop();
        check(dut.u_seq.x == 16'(at(p_mov)), $sformatf("MOV X, C in cycle %0d gives X = %0d", at(p_mov), dut.u_seq.x));
        check(at(p_mov2) - at(p_tmr) == 4 && dut.u_seq.y == 16'd3,
              $sformatf("TMR cclr [3], then MOV Y, C: Y = %0d (C reads 0 in the cycle after TMR)", dut.u_seq.y));

        // 2. guard idiom: TMR cclr, gsel=1 at odd pc p, WAIT GUARD at p + 1 (even), successor in its word
        pq.delete();
        emit(DLY(2));
        p_g = pq.size(); emit(TMR(1, 1, 1, 0));
        emit(WAITI(1, tse_isa_pkg::WSRC_FLAG, tse_isa_pkg::WIDX_FLAG_GUARD, 0, 0, 0));
        emit(DLY(0));
        // 3. JMP GUARD with gsel 0: falls through at C < GT0, jumps after
        emit(TMR(1, 1, 0, 0));
        p_j = pq.size(); emit(JMP(tse_isa_pkg::COND_GUARD, 0));
        emit(DLY(GT0));
        emit(JMP(tse_isa_pkg::COND_GUARD, pq.size() + 2));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); clear_disp(); start(); repeat (120) @(negedge clk); stop();
        check(at(p_g + 2) == at(p_g) + 1 + GT1 + 1,
              $sformatf("TMR cclr, gsel=1 in cycle %0d: WAIT GUARD ends in cycle %0d (c + 1 + GT1 = %0d)",
                        at(p_g), at(p_g + 2) - 1, at(p_g) + 1 + GT1));
        check(at(p_j + 1) >= 0 && at(p_j + 4) >= 0 && at(p_j + 3) < 0, "JMP GUARD falls through before GT0, jumps after");

        // 4. CSRR CLOCK and TS
        pq.delete();
        emit(DLY(10));
        p_rd = pq.size(); emit(CSRR(tse_regs_pkg::LR_CLOCK));
        emit(MOV(tse_isa_pkg::MOVDST_ISR, tse_isa_pkg::MOVSRC_X, 0));
        p_ts = pq.size(); emit(CSRR(tse_regs_pkg::LR_TS));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        ts_pc = p_ts;
        load_q(); clear_disp(); start(); repeat (40) @(negedge clk); stop();
        check(dut.u_seq.isr == 32'(at(p_rd)), $sformatf("CSRR CLOCK in cycle %0d reads %0d", at(p_rd), dut.u_seq.isr));
        check(yx() == ts_seen, $sformatf("CSRR TS reads the timestamp of its cycle (%0d)", ts_seen));

        // 5. WAIT CHIP: TRIG (index 4) from the chip flags; index 6 reads 0 (times out); SYNCED
        pq.delete();
        emit(SET(tse_isa_pkg::SETDST_T, 20));
        p_w = pq.size(); emit(WAITI(1, tse_isa_pkg::WSRC_CHIP, tse_isa_pkg::WIDX_CHIP_TRIG, 0, 0, 0));
        emit(WAITI(1, tse_isa_pkg::WSRC_CHIP, 6, 1, 0, 0));
        emit(JMP(tse_isa_pkg::COND_TOUT, pq.size() + 2));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        p_s = pq.size(); emit(WAITI(1, tse_isa_pkg::WSRC_FLAG, tse_isa_pkg::WIDX_FLAG_SYNCED, 1, 0, 0));
        emit(SET(tse_isa_pkg::SETDST_CMD, 1 << tse_regs_pkg::LR_CMD_HARD_ARM_LO));
        emit(WAITI(1, tse_isa_pkg::WSRC_FLAG, tse_isa_pkg::WIDX_FLAG_SYNCED, 1, 0, 0));
        emit(JMP(tse_isa_pkg::COND_TOUT, pq.size() + 2));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); clear_disp(); chip_flags = 6'b11_0000 ^ 6'b01_0000; start();
        repeat (30) @(negedge clk);
        check(at(p_w + 1) < 0, "WAIT CHIP TRIG holds while TRIG is 0");
        chip_flags = 6'b01_0000;
        repeat (120) @(negedge clk); stop();
        check(at(p_w + 1) >= 0 && at(p_w + 4) >= 0 && at(p_w + 3) < 0, "WAIT CHIP TRIG ends on the flag; CHIP index 6 reads 0 (timeout)");
        check(at(p_s + 1) - at(p_s) <= 4 && at(p_s + 5) >= 0 && at(p_s + 4) < 0,
              "FLAG SYNCED is 1 before CMD.hard_arm and 0 after (the second WAIT times out)");
        finish("tb_lane_clock");
    end
endmodule
