`timescale 1ns/1ps
// WAIT: PIN indices 4 to 7 end on any change of the pin after dispatch (either
// direction, in the cycle the change is visible); the alternate condition can
// be a pin change too; TFLAG and AFLAG clear when a WAIT dispatches; a WAIT 1,
// EV, 0 satisfied by the timer clears the TIMER flag, so the next one waits
// for the next expiry; the successor of a long WAIT is fetched while it waits,
// so it dispatches in the cycle after the WAIT ends even from another word.
module tb_lane_wait;
`include "lane_h.svh"

    task automatic pin_at(input int k, input int i, input logic v);   // vin[i] = v visible from cycle k
        while (cyc < k - 1) @(negedge clk);
        @(negedge clk) vin[i] = v;
    endtask

    longint tflag_wait;           // cycles with TFLAG set while the second WAIT was waiting
    always @(posedge clk) if (run && dut.u_seq.ic == tse_isa_pkg::I_WAIT && dut.u_seq.held && dut.u_seq.tflag) tflag_wait++;

    initial begin
        int p1, p2, pa, pt, pf;
        repeat (3) @(negedge clk); rst_n = 1;
        repeat (2) @(negedge clk);

        // 1. pin-change waits: rising then falling change of vin[1], each successor in the WAIT's word
        pq.delete();
        emit(DLY(0));
        p1 = pq.size(); emit(WAITI(1, tse_isa_pkg::WSRC_PIN, 4 + 1, 0, 0, 0)); emit(DLY(0));
        p2 = pq.size(); emit(WAITI(1, tse_isa_pkg::WSRC_PIN, 4 + 1, 0, 0, 0)); emit(DLY(0));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); clear_disp(); vin = 0; start();
        pin_at(30, 1, 1);
        pin_at(50, 1, 0);
        repeat (20) @(negedge clk); stop();
        check(at(p1 + 1) == 31 && at(p2 + 1) == 51,
              $sformatf("WAIT 1, PIN, 5 ends in the cycle pin 1 rises (successor %0d) and falls (successor %0d)",
                        at(p1 + 1), at(p2 + 1)));

        // 2. alternate condition "pin 2 changed": WAIT 1, PIN, 0, alt ends with AFLAG, JMP TOUT taken
        cfg_set(tse_regs_pkg::LR_LANE_CFG, (1 << tse_regs_pkg::LR_LANE_CFG_ALT_POL_LO)
                | (tse_isa_pkg::WSRC_PIN << tse_regs_pkg::LR_LANE_CFG_ALT_SRC_LO) | ((4 + 2) << tse_regs_pkg::LR_LANE_CFG_ALT_IDX_LO));
        pq.delete();
        emit(DLY(0));
        pa = pq.size(); emit(WAITI(1, tse_isa_pkg::WSRC_PIN, 0, 0, 1, 0));
        emit(JMP(tse_isa_pkg::COND_TOUT, pq.size() + 2));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); clear_disp(); vin = 4'b0100; start();
        pin_at(30, 2, 0);
        repeat (20) @(negedge clk); stop();
        check(at(pa + 1) >= 0 && at(pa + 3) >= 0 && at(pa + 2) < 0 && dut.u_seq.aflag,
              "alternate pin 2 changed (falling) ends WAIT PIN 0 with AFLAG; JMP TOUT taken");

        // 3. TFLAG clears at dispatch: a timed-out WAIT, then a WAIT that waits for pin 0
        pq.delete();
        emit(SET(tse_isa_pkg::SETDST_T, 3));
        emit(WAITI(1, tse_isa_pkg::WSRC_PIN, 0, 1, 0, 0));
        pt = pq.size(); emit(WAITI(1, tse_isa_pkg::WSRC_PIN, 0, 0, 0, 0));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); clear_disp(); vin = 0; tflag_wait = 0; start();
        repeat (30) @(negedge clk);
        check(at(pt) >= 0 && ndisp[pt + 1] == 0 && tflag_wait == 0 && !dut.u_seq.tflag,
              "TFLAG of the timed-out WAIT reads 0 from the next WAIT's dispatch on");
        stop();

        // 4. TIMER flag: expiry ends WAIT 1, EV, 0 and clears; the next one times out
        cfg_set(tse_regs_pkg::LR_TIMER_CTL, (1 << tse_regs_pkg::LR_TIMER_CTL_EV0_SEL_LO)
                | (1 << tse_regs_pkg::LR_TIMER_CTL_EN_LO) | 10);
        cfg_set(tse_regs_pkg::LR_LANE_CFG, 0);
        pq.delete();
        emit(SET(tse_isa_pkg::SETDST_T, 0)); emit(SET(tse_isa_pkg::SETDST_T, 40));
        emit(SET(tse_isa_pkg::SETDST_CMD, 1 << tse_regs_pkg::LR_CMD_FRAME_RST_LO));   // timer starts
        pf = pq.size(); emit(WAITI(1, tse_isa_pkg::WSRC_EV, tse_isa_pkg::WIDX_EV_TIMER, 0, 0, 0));
        emit(WAITI(1, tse_isa_pkg::WSRC_EV, tse_isa_pkg::WIDX_EV_TIMER, 1, 0, 0));
        emit(JMP(tse_isa_pkg::COND_TOUT, pq.size() + 2));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); clear_disp(); start();
        repeat (100) @(negedge clk); stop();
        check(at(pf + 1) >= 0 && at(pf + 1) - at(pf) >= 10 && at(pf + 2) - at(pf + 1) == 41 && at(pf + 4) >= 0 && at(pf + 3) < 0
              && !dut.tmr_flag,
              $sformatf("WAIT on the TIMER flag lasts %0d cycles and clears it: the next WAIT times out after %0d",
                        at(pf + 1) - at(pf), at(pf + 2) - at(pf + 1)));

        // 5. a long WAIT at an odd pc: its successor (next word) dispatches in the cycle after it ends
        cfg_set(tse_regs_pkg::LR_TIMER_CTL, 0);
        pq.delete();
        emit(DLY(0)); emit(DLY(0)); emit(DLY(0));
        pt = pq.size(); emit(WAITI(1, tse_isa_pkg::WSRC_PIN, 3, 0, 0, 0));
        emit(DLY(0));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        for (int ph = 0; ph < 4; ph++) begin
            load_q(); clear_disp(); vin = 0; start();
            pin_at(30 + ph, 3, 1);
            repeat (10) @(negedge clk); stop();
            check(at(pt + 1) == 31 + ph, $sformatf("WAIT ends in cycle %0d, successor in the next word dispatches in %0d",
                                                 30 + ph, at(pt + 1)));
        end
        finish("tb_lane_wait");
    end
endmodule
