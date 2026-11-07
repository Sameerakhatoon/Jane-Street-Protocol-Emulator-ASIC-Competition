// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps
// LDW and STW on the data window. For every phase of the dispatch cycle c
// against the 16-cycle data frame: STW lasts s + 1 - c and LDW s + 2 - c,
// with s the lane's first data cycle at or after c (s mod 16 == 4 x lane +
// 3), unless the successor's word (fetched from c on, in the first own slot
// f >= c) arrives later, f + 1; STW stores ISR, LDW loads OSR (by address and
// through X[6:0]).
module tb_lane_data;
`include "lane_h.svh"

    function automatic logic [15:0] STW(input int ind, a);
        return tse_isa_pkg::STW_MATCH | f(ind, tse_isa_pkg::STW_IND_LO) | f(a, tse_isa_pkg::STW_ADDR_LO);
    endfunction
    function automatic logic [15:0] LDW(input int ind, a);
        return tse_isa_pkg::LDW_MATCH | f(ind, tse_isa_pkg::LDW_IND_LO) | f(a, tse_isa_pkg::LDW_ADDR_LO);
    endfunction
    function automatic longint fetch_after(input longint c);
        longint f;
        f = c;
        while (f % tse_isa_pkg::FRAME_SLOTS != LANE) f++;
        return f;
    endfunction
    function automatic longint mx(input longint a, b); return a > b ? a : b; endfunction
    function automatic longint slot_after(input longint c);
        longint s;
        s = c;
        while (s % tse_isa_pkg::DATA_FRAME != tse_isa_pkg::FRAME_SLOTS * LANE + tse_isa_pkg::FRAME_SLOTS - 1) s++;
        return s;
    endfunction

    logic [31:0] osr_at7, osr_end;
    always @(posedge clk) if (run && dut.u_seq.avail && !dut.u_seq.held && dut.u_seq.pc == 8) osr_at7 <= dut.u_seq.osr;

    initial begin
        int ok_st, ok_ld, ok_x, ok_d;
        logic [16:0] seen;
        ok_st = 0; ok_ld = 0; ok_x = 0; ok_d = 0; seen = 0;
        repeat (3) @(negedge clk); rst_n = 1;
        repeat (2) @(negedge clk);
        for (int k = 0; k < 16; k++) begin
            longint c, s;
            pq.delete();
            emit(SET(tse_isa_pkg::SETDST_X, 8'h34 + k));                    // 0
            emit(SET(tse_isa_pkg::SETDST_XH, 8'h12));                       // 1
            emit(MOV(tse_isa_pkg::MOVDST_ISR, tse_isa_pkg::MOVSRC_X, 0));   // 2
            emit(DLY(0));                                                   // 3
            emit(DLY(k));         emit(STW(0, 5));                          // 4, 5
            emit(DLY(k));         emit(LDW(0, 6));                          // 6, 7
            emit(SET(tse_isa_pkg::SETDST_X, 7)); emit(LDW(1, 0));           // 8, 9
            emit(DLY(0));         emit(DLY(0));                             // 10, 11
            emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
            load_q();
            dwin[5] = 32'h0; dwin[6] = 32'hCAFE_BA00 + k; dwin[7] = 32'h5EED_0000 + k;
            clear_disp(); start(); repeat (90) @(negedge clk); osr_end = dut.u_seq.osr; stop();
            c = at(5); s = slot_after(c);
            seen[int'(s - c)] = 1'b1;
            if (at(6) == mx(s + 1, fetch_after(c) + 1) && dwin[5] == {16'd0, 8'h12, 8'(8'h34 + k)}) ok_st++;
            else $display("FAIL phase %0d: STW in %0d, slot %0d, successor %0d, stored %h", k, c, s, at(6), dwin[5]);
            c = at(7); s = slot_after(c);
            if (at(8) == mx(s + 2, fetch_after(c) + 1) && osr_at7 == 32'hCAFE_BA00 + k) ok_ld++;
            else $display("FAIL phase %0d: LDW in %0d, slot %0d, successor %0d, OSR %h", k, c, s, at(8), osr_at7);
            c = at(9); s = slot_after(c);
            if (at(10) == mx(s + 2, fetch_after(c) + 1) && osr_end == 32'h5EED_0000 + k) ok_x++;
            else $display("FAIL phase %0d: LDW X in %0d, successor %0d, OSR %h", k, at(9), at(10), osr_end);
        end
        check(ok_st == 16, $sformatf("lane %0d: STW lasts s + 1 - c (or until the successor word) and stores ISR in %0d of 16 phases", LANE, ok_st));
        check(ok_ld == 16, $sformatf("lane %0d: LDW lasts s + 2 - c (or until the successor word) and loads OSR in %0d of 16 phases", LANE, ok_ld));
        check(ok_x == 16, $sformatf("lane %0d: LDW X reads the word at X[6:0] in %0d of 16 phases", LANE, ok_x));
        check(seen[15:0] == 16'hFFFF, $sformatf("STW waits of 0 to 15 cycles all occurred (%b)", seen[15:0]));
        if (errors != 0 || ok_st + ok_ld + ok_x != 48) errors++;
        finish("tb_lane_data");
    end
endmodule
