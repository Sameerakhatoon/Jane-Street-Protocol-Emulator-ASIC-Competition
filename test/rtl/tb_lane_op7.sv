// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps
// Opcode 7: DLY d lasts 1 + d (9-bit d), TMR lasts 1 + delay, SIG drives the
// chip-flag write (mask, val) in its own cycle and lasts 1, EMIT lasts
// 1 + delay and hands (6-bit code, X[7:0]) to the event unit in the next
// cycle, also for codes above 15; SET IRQ pulses the interrupt bits and
// raises no event. Every measured instruction sits at an even pc so its
// successor is in the same word and no fetch stall enters the cost.
module tb_lane_op7;
`include "lane_h.svh"

    function automatic logic [15:0] TMR(input int cclr, gwe, gsel, d);
        return tse_isa_pkg::TMR_MATCH | f(cclr, tse_isa_pkg::TMR_CCLR_LO) | f(gwe, tse_isa_pkg::TMR_GWE_LO)
             | f(gsel, tse_isa_pkg::TMR_GSEL_LO) | f(d, tse_isa_pkg::TMR_DELAY_LO);
    endfunction
    function automatic logic [15:0] SIG(input int m, v);
        return tse_isa_pkg::SIG_MATCH | f(m, tse_isa_pkg::SIG_MASK_LO) | f(v, tse_isa_pkg::SIG_VAL_LO);
    endfunction
    function automatic logic [15:0] EMIT(input int c, d);
        return tse_isa_pkg::EMIT_MATCH | f(c, tse_isa_pkg::EMIT_CODE_LO) | f(d, tse_isa_pkg::EMIT_DELAY_LO);
    endfunction

    // what the lane hands out, by cycle
    longint sig_at [$], ev_at [$], irq_at [$];
    logic [7:0]  sig_mv [$];
    logic [13:0] ev_cd [$];
    always @(posedge clk) if (run) begin
        if (sig_we) begin sig_at.push_back(cyc); sig_mv.push_back({sig_mask, sig_val}); end
        if (ev_valid) begin ev_at.push_back(cyc); ev_cd.push_back({ev_code, ev_data}); end
        if (irq != 0) irq_at.push_back(cyc);
    end

    initial begin
        int p_dly, p_dmax, p_tmr, p_sig, p_em1, p_em2, p_irq;
        repeat (3) @(negedge clk); rst_n = 1;
        repeat (2) @(negedge clk);
        emit(SET(tse_isa_pkg::SETDST_X, 8'hA7)); emit(DLY(0));
        p_dly  = pq.size(); emit(DLY(9));        emit(DLY(0));
        p_dmax = pq.size(); emit(DLY(511));      emit(DLY(0));
        p_tmr  = pq.size(); emit(TMR(0, 0, 0, 6)); emit(DLY(0));
        p_sig  = pq.size(); emit(SIG(4'b1010, 4'b0011)); emit(DLY(0));
        p_em1  = pq.size(); emit(EMIT(6'h3D, 5)); emit(DLY(0));
        p_em2  = pq.size(); emit(EMIT(6'h05, 0)); emit(DLY(0));
        p_irq  = pq.size(); emit(SET(tse_isa_pkg::SETDST_IRQ, 8'h09)); emit(DLY(0));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); clear_disp(); start();
        repeat (700) @(negedge clk);
        stop();
        check(at(p_dly + 1) - at(p_dly) == 10, $sformatf("DLY 9 lasts %0d cycles", at(p_dly + 1) - at(p_dly)));
        check(at(p_dmax + 1) - at(p_dmax) == 512, $sformatf("DLY 511 lasts %0d cycles", at(p_dmax + 1) - at(p_dmax)));
        check(at(p_tmr + 1) - at(p_tmr) == 7, $sformatf("TMR [6] lasts %0d cycles", at(p_tmr + 1) - at(p_tmr)));
        check(at(p_sig + 1) - at(p_sig) == 1 && sig_at.size() == 1 && sig_at[0] == at(p_sig) && sig_mv[0] == 8'hA3,
              $sformatf("SIG lasts 1 cycle and writes mask 1010 val 0011 in its own cycle (%0d writes)", sig_at.size()));
        check(at(p_em1 + 1) - at(p_em1) == 6 && at(p_em2 + 1) - at(p_em2) == 1,
              $sformatf("EMIT [5] lasts %0d cycles, EMIT lasts %0d", at(p_em1 + 1) - at(p_em1), at(p_em2 + 1) - at(p_em2)));
        check(ev_at.size() == 2 && ev_at[0] == at(p_em1) + 1 && ev_cd[0] == {6'h3D, 8'hA7}
              && ev_at[1] == at(p_em2) + 1 && ev_cd[1] == {6'h05, 8'hA7},
              $sformatf("EMIT hands code 0x3D and 0x05 with X[7:0] = 0xA7 to the event unit one cycle later (%0d events)",
                        ev_at.size()));
        check(irq_at.size() == 1 && irq_at[0] == at(p_irq) + 1 && ev_at.size() == 2,
              "SET IRQ pulses the interrupt bits one cycle later and raises no event");
        finish("tb_lane_op7");
    end
endmodule
