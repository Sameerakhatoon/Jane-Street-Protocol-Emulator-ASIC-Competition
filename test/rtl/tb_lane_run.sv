`timescale 1ns/1ps
// RUN starts from a clean programmer's model without a reset in between: a
// program leaves X, Y, T, ISR, OSR, PINS, PINDIRS and TFLAG nonzero, the lane
// stops and runs again, and in its first running cycle all of them read 0
// (PC at start_pc, C at 0, as before). A second program then reads them.
module tb_lane_run;
`include "lane_h.svh"

    function automatic bit clean();
        return dut.u_seq.x == 0 && dut.u_seq.y == 0 && dut.u_seq.t == 0 && dut.u_seq.tc == 0
            && dut.u_seq.isr == 0 && dut.u_seq.osr == 0 && !dut.u_seq.tflag && !dut.u_seq.aflag
            && vout === 4'd0 && voe === 4'd0 && dut.u_seq.clock == 0;
    endfunction

    initial begin
        int p_end;
        repeat (3) @(negedge clk); rst_n = 1;
        repeat (2) @(negedge clk);

        pq.delete();
        emit(SET(tse_isa_pkg::SETDST_X, 8'h5A));
        emit(SET(tse_isa_pkg::SETDST_Y, 8'hA5));
        emit(SET(tse_isa_pkg::SETDST_T, 3));
        emit(MOV(tse_isa_pkg::MOVDST_OSR, tse_isa_pkg::MOVSRC_X, 0));
        emit(IN(tse_isa_pkg::INSRC_ONES, 12, 0));
        emit(SET(tse_isa_pkg::SETDST_PINDIRS, 8'hFF));
        emit(SET(tse_isa_pkg::SETDST_PINS, 8'hF9));
        emit(WAITI(1, tse_isa_pkg::WSRC_PIN, 0, 1, 0, 0));        // times out: TFLAG
        p_end = pq.size(); emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); clear_disp(); vin = 0; start();
        repeat (60) @(negedge clk);
        check(at(p_end) >= 0 && dut.u_seq.x == 16'h5A && dut.u_seq.y == 16'hA5 && dut.u_seq.osr == 32'h5A
              && dut.u_seq.isr == 32'hFFF && dut.u_seq.tflag && vout == 4'h9 && voe == 4'hF,
              "first run leaves X, Y, OSR, ISR, TFLAG, PINS and PINDIRS set");
        stop();
        @(negedge clk) run = 1;
        #1 check(clean(), "second run, first cycle: X, Y, T, ISR, OSR, flags, PINS, PINDIRS and C read 0");

        // the second run's program sees the cleared registers
        stop();
        pq.delete();
        emit(MOV(tse_isa_pkg::MOVDST_ISR, tse_isa_pkg::MOVSRC_Y, 0));
        emit(IN(tse_isa_pkg::INSRC_X, 16, 0));
        emit(OUT(tse_isa_pkg::OUTDST_X, 16, 0));
        p_end = pq.size(); emit(JMP(tse_isa_pkg::COND_TOUT, pq.size() + 2));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); clear_disp(); start();
        repeat (30) @(negedge clk);
        check(dut.u_seq.isr == 0 && dut.u_seq.x == 0 && at(p_end + 1) >= 0 && at(p_end + 2) < 0,
              "programs read Y, X and OSR as 0 and JMP TOUT falls through (TFLAG 0)");
        stop();
        finish("tb_lane_run");
    end
endmodule
