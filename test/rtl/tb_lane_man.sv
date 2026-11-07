// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps
// Manchester transmit on the lane's codec, DPLL bit timer free running:
//  1. at 6 cycles per bit every line change is 3 or 6 cycles after the
//     previous one (each half bit is exactly half the bit);
//  2. a frame written between a bit start and that bit's half strobe leaves
//     the line at its idle level until the frame's first bit, at 20 cycles
//     per bit, after a frame whose last bit was 1.
module tb_lane_man;
`include "lane_h.svh"

    localparam int TXPIN = 0;
    localparam logic [31:0] LCFG = (1 << tse_regs_pkg::LR_LANE_CFG_TX_OWNS_PIN_LO) | TXPIN;
    localparam int MAN = 2;

    // line changes and frame starts, recorded while running
    longint chg [$];
    longint first_step;
    int     frames;
    logic   line_q, act_q, stepped, idle_bad, before_half;
    always @(posedge clk) begin
        line_q <= vout[TXPIN];
        act_q  <= dut.tx_active;
        if (run && vout[TXPIN] !== line_q && stepped) chg.push_back(cyc);
        if (run && dut.tx_active && !act_q) begin
            frames++; stepped = 0;
            before_half = !dut.u_dpll.h_done && !dut.u_dpll.half;   // the write fell before this bit's half
        end
        if (run && dut.tx_step && !stepped) begin stepped = 1; first_step = cyc; end
        // between the TXDATA write and the frame's first bit the line keeps the idle level 0
        if (run && dut.tx_active && !stepped && vout[TXPIN] !== 1'b0) idle_bad = 1;
    end

    task automatic setup(input int period);
        cfg_set(tse_regs_pkg::LR_LANE_CFG, LCFG);
        cfg_set(tse_regs_pkg::LR_CODEC_CFG, MAN);
        cfg_set(tse_regs_pkg::LR_DPLL_PERIOD, period);
        chg.delete(); frames = 0; stepped = 0; idle_bad = 0;
    endtask

    initial begin
        int bad;
        repeat (3) @(negedge clk); rst_n = 1;
        repeat (2) @(negedge clk);

        // 1. one byte 0xA5 at 6 cycles per bit
        setup(96);
        pq.delete();
        emit(SET(tse_isa_pkg::SETDST_CMD, 1 << tse_regs_pkg::LR_CMD_DPLL_ON_LO));
        emit(SET(tse_isa_pkg::SETDST_X, 7));
        emit(CSRW(tse_regs_pkg::LR_TXCTL));
        emit(SET(tse_isa_pkg::SETDST_X, 8'hA5));
        emit(CSRW(tse_regs_pkg::LR_TXDATA));
        emit(WAITI(1, tse_isa_pkg::WSRC_EV, tse_isa_pkg::WIDX_EV_TXIDLE, 0, 0, 0));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); start(); repeat (120) @(negedge clk); stop();
        bad = 0;
        for (int i = 1; i < chg.size(); i++)
            if (chg[i] - chg[i - 1] != 3 && chg[i] - chg[i - 1] != 6) bad++;
        check(frames == 1 && chg.size() >= 10 && bad == 0,
              $sformatf("Manchester at 6 cycles per bit: %0d line changes, %0d not 3 or 6 cycles apart",
                        chg.size(), bad));

        // 2. frame 1 is one bit 1; frame 2 is written just after a bit start
        setup(320);
        pq.delete();
        emit(SET(tse_isa_pkg::SETDST_CMD, 1 << tse_regs_pkg::LR_CMD_DPLL_ON_LO));
        emit(SET(tse_isa_pkg::SETDST_X, 0));
        emit(CSRW(tse_regs_pkg::LR_TXCTL));
        emit(SET(tse_isa_pkg::SETDST_X, 1));
        emit(CSRW(tse_regs_pkg::LR_TXDATA));
        emit(WAITI(1, tse_isa_pkg::WSRC_EV, tse_isa_pkg::WIDX_EV_TXIDLE, 0, 0, 0));
        emit(WAITI(1, tse_isa_pkg::WSRC_EV, tse_isa_pkg::WIDX_EV_BITSTART, 0, 0, 0));
        emit(WAITI(1, tse_isa_pkg::WSRC_EV, tse_isa_pkg::WIDX_EV_BITSTART, 0, 0, 0));
        emit(CSRW(tse_regs_pkg::LR_TXDATA));
        emit(WAITI(1, tse_isa_pkg::WSRC_EV, tse_isa_pkg::WIDX_EV_TXIDLE, 0, 0, 0));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); start(); repeat (200) @(negedge clk); stop();
        check(frames == 2 && before_half && !idle_bad,
              $sformatf("frame written before a half strobe (%0d): %0d frames, line left idle before the first bit: %0d",
                        before_half, frames, !idle_bad));
        finish("tb_lane_man");
    end
endmodule
