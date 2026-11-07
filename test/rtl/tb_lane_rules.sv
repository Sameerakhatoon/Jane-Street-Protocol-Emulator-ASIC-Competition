// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps
// Rule chaining: with RULE1_CTL.chain, rule 1 matches only after rule 0 has
// matched earlier in the frame (two rules then check a 16-bit pattern pair);
// without chain the same rule 1 matches on its own. SET PINS writes imm[3:0]
// under the mask imm[7:4]. Illegal words (OUT selector 6, a PUSH reserved
// bit, LDW with ind and a nonzero address) halt the lane at dispatch with no
// effect, pulse interrupt bit 3 once and stop fetching.
module tb_lane_rules;
`include "lane_h.svh"

    // ---------------- two rule blocks fed the same bits: chain on and off
    localparam logic [13:0] CTL0 = 14'(1 << tse_regs_pkg::LR_RULE0_CTL_EN_LO);
    localparam logic [13:0] CTL1 = 14'((1 << tse_regs_pkg::LR_RULE1_CTL_EN_LO) | (1 << tse_regs_pkg::LR_RULE1_CTL_CHAIN_LO));
    logic frst = 0, bv = 0, bi = 0;
    logic [1:0] hit_c, hit_n;
    tse_resp_rule #(.K(2), .PW(16)) rc (.clk, .rst_n, .cfg_pat({16'h003C, 16'h00A5}), .cfg_msk({16'h00FF, 16'h00FF}),
        .cfg_pos({9'h100, 9'h100}), .cfg_resp('0), .cfg_ctl({CTL1, CTL0}),
        .cfg_src(1'b0), .cfg_edge(1'b0), .cfg_tdly(8'd0), .cfg_hold(1'b0), .seq_wr(1'b0), .frame_rst(frst), .bit_valid(bv), .bit_in(bi),
        .bit_start(1'b0), .pin_clk(1'b0), .pin_dat(1'b0), .crc_ok(1'b0), .ext_flag(1'b0), .flags_clr(frst),
        .drv_en(), .drv_val(), .hit_flags(hit_c), .hit_pulse(), .abort_pulse());
    tse_resp_rule #(.K(2), .PW(16)) rn (.clk, .rst_n, .cfg_pat({16'h003C, 16'h00A5}), .cfg_msk({16'h00FF, 16'h00FF}),
        .cfg_pos({9'h100, 9'h100}), .cfg_resp('0), .cfg_ctl({CTL1 & ~14'(1 << tse_regs_pkg::LR_RULE1_CTL_CHAIN_LO), CTL0}),
        .cfg_src(1'b0), .cfg_edge(1'b0), .cfg_tdly(8'd0), .cfg_hold(1'b0), .seq_wr(1'b0), .frame_rst(frst), .bit_valid(bv), .bit_in(bi),
        .bit_start(1'b0), .pin_clk(1'b0), .pin_dat(1'b0), .crc_ok(1'b0), .ext_flag(1'b0), .flags_clr(frst),
        .drv_en(), .drv_val(), .hit_flags(hit_n), .hit_pulse(), .abort_pulse());

    task automatic send_byte(input logic [7:0] b);
        for (int i = 7; i >= 0; i--) begin
            @(negedge clk) bi = b[i]; bv = 1;
            @(negedge clk) bv = 0;
        end
        @(negedge clk);
    endtask

    function automatic logic [15:0] OUTI(input int d); return OUT(d, 1, 0); endfunction

    int irqs;
    always @(posedge clk) if (irq == 4'b1000) irqs++;

    initial begin
        logic [15:0] bad [3];
        repeat (3) @(negedge clk); rst_n = 1;
        repeat (2) @(negedge clk);

        // 1. chaining
        @(negedge clk) frst = 1; @(negedge clk) frst = 0;
        send_byte(8'h3C);
        check(hit_c == 2'b00 && hit_n == 2'b10, "0x3C before 0xA5: chained rule 1 does not match, unchained does");
        send_byte(8'hA5);
        check(hit_c == 2'b01, "0xA5 sets rule 0's hit flag");
        send_byte(8'h3C);
        check(hit_c == 2'b11, "0x3C after 0xA5: chained rule 1 matches");
        @(negedge clk) frst = 1; @(negedge clk) frst = 0;
        send_byte(8'h3C);
        check(hit_c == 2'b00, "frame reset clears the chain: 0x3C alone does not match again");

        // 2. SET PINS with a write mask
        pq.delete();
        emit(SET(tse_isa_pkg::SETDST_PINDIRS, 8'hFF));
        emit(SET(tse_isa_pkg::SETDST_PINS, 8'hFF));
        emit(SET(tse_isa_pkg::SETDST_PINS, 8'h65));         // mask 0110, value 0101
        emit(SET(tse_isa_pkg::SETDST_PINDIRS, 8'h80));      // mask 1000, value 0000
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); start(); repeat (20) @(negedge clk);
        check(vout === 4'b1101 && voe === 4'b0111, $sformatf("SET with masks: pins %b dirs %b (want 1101, 0111)", vout, voe));
        stop();

        // 3. illegal words halt the lane
        bad[0] = OUTI(6);
        bad[1] = tse_isa_pkg::PUSH_MATCH | 16'h0001;
        bad[2] = tse_isa_pkg::LDW_MATCH | f(1, tse_isa_pkg::LDW_IND_LO) | f(3, tse_isa_pkg::LDW_ADDR_LO);
        foreach (bad[i]) begin
            pq.delete();
            emit(SET(tse_isa_pkg::SETDST_X, 1)); emit(DLY(0));
            emit(bad[i]);
            emit(SET(tse_isa_pkg::SETDST_X, 2));
            emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
            load_q(); clear_disp(); irqs = 0; start();
            repeat (12) @(negedge clk);
            begin
                int f0;
                f0 = fetches;
                repeat (20) @(negedge clk);
                check(halted && at(2) >= 0 && at(3) < 0 && dut.u_seq.x == 16'd1 && irqs == 1 && fetches == f0
                      && !tse_isa_pkg::tse_legal(bad[i]),
                      $sformatf("illegal word %h halts the lane at dispatch, IRQ bit 3 once, no more fetches", bad[i]));
            end
            stop();
            check(!halted, "stopping the lane clears halted");
        end
        finish("tb_lane_rules");
    end
endmodule
