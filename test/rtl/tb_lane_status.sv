// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps
// Lane STATUS in the register-table layout, read by the program (CSRR into
// {Y, X}) and by the host: the eight EV levels, TFLAG, AFLAG, running, the
// TIMER flag and the PC; after an illegal word, illegal set, running clear
// and the PC of the illegal word.
module tb_lane_status;
`include "lane_h.svh"

    function automatic logic [31:0] fld(input logic [31:0] v, input int hi, lo);
        return (v >> lo) & ((32'd1 << (hi - lo + 1)) - 1);
    endfunction
    function automatic logic [31:0] yx(); return {dut.u_seq.y, dut.u_seq.x}; endfunction

    initial begin
        logic [31:0] st;
        int p_rd;
        repeat (3) @(negedge clk); rst_n = 1;
        repeat (2) @(negedge clk);
        cfg_set(tse_regs_pkg::LR_TIMER_CTL, (1 << tse_regs_pkg::LR_TIMER_CTL_EN_LO) | 4);

        // a timed-out WAIT sets TFLAG, the timer expires (TIMER flag), then CSRR STATUS
        pq.delete();
        emit(SET(tse_isa_pkg::SETDST_T, 0)); emit(SET(tse_isa_pkg::SETDST_T, 2));
        emit(SET(tse_isa_pkg::SETDST_CMD, 1 << tse_regs_pkg::LR_CMD_FRAME_RST_LO));
        emit(WAITI(1, tse_isa_pkg::WSRC_PIN, 0, 1, 0, 0));
        emit(DLY(20));
        p_rd = pq.size(); emit(CSRR(tse_regs_pkg::LR_STATUS));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); vin = 0; start(); repeat (60) @(negedge clk);
        st = yx();
        check(fld(st, tse_regs_pkg::LR_STATUS_TFLAG_HI, tse_regs_pkg::LR_STATUS_TFLAG_LO) == 1
              && fld(st, tse_regs_pkg::LR_STATUS_AFLAG_HI, tse_regs_pkg::LR_STATUS_AFLAG_LO) == 0,
              $sformatf("CSRR STATUS %h: TFLAG 1, AFLAG 0 after a timeout", st));
        check(fld(st, tse_regs_pkg::LR_STATUS_RUNNING_HI, tse_regs_pkg::LR_STATUS_RUNNING_LO) == 1
              && fld(st, tse_regs_pkg::LR_STATUS_ILLEGAL_HI, tse_regs_pkg::LR_STATUS_ILLEGAL_LO) == 0,
              "running 1, illegal 0");
        check(fld(st, tse_regs_pkg::LR_STATUS_TIMER_HI, tse_regs_pkg::LR_STATUS_TIMER_LO) == 1, "TIMER flag set by the expiry");
        check(fld(st, tse_regs_pkg::LR_STATUS_PC_HI, tse_regs_pkg::LR_STATUS_PC_LO) == p_rd,
              $sformatf("PC field %0d is the CSRR's own pc", fld(st, tse_regs_pkg::LR_STATUS_PC_HI, tse_regs_pkg::LR_STATUS_PC_LO)));
        check(fld(st, tse_regs_pkg::LR_STATUS_EV_HI, tse_regs_pkg::LR_STATUS_EV_LO)
              == ((1 << tse_isa_pkg::WIDX_EV_TXNEED) | (1 << tse_isa_pkg::WIDX_EV_TXIDLE) | (1 << tse_isa_pkg::WIDX_EV_CRCOK)),
              $sformatf("EV levels %b: TXNEED and TXIDLE (TX idle), CRCOK (CRC 0 matches check 0)", st[7:0]));

        // host read of the same register agrees with the program read
        @(negedge clk) h_addr = tse_regs_pkg::LR_STATUS;
        #1 check(h_rdata[23:0] == st[23:0] && fld(h_rdata, tse_regs_pkg::LR_STATUS_PC_HI, tse_regs_pkg::LR_STATUS_PC_LO) == p_rd + 1,
                 "host STATUS read equals the program read, PC now at the parking jump");
        stop();

        // an illegal word: illegal 1, running 0, PC at the word
        pq.delete();
        emit(DLY(0)); emit(DLY(0)); emit(OUT(7, 1, 0));
        load_q(); start(); repeat (20) @(negedge clk);
        #1 st = h_rdata;
        check(fld(st, tse_regs_pkg::LR_STATUS_ILLEGAL_HI, tse_regs_pkg::LR_STATUS_ILLEGAL_LO) == 1
              && fld(st, tse_regs_pkg::LR_STATUS_RUNNING_HI, tse_regs_pkg::LR_STATUS_RUNNING_LO) == 0
              && fld(st, tse_regs_pkg::LR_STATUS_PC_HI, tse_regs_pkg::LR_STATUS_PC_LO) == 2,
              $sformatf("after OUT selector 7: STATUS %h shows illegal, not running, PC 2", st));
        stop();
        finish("tb_lane_status");
    end
endmodule
