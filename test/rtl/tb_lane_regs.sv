`timescale 1ns/1ps
// Lane registers by name: a program writes every class-L register with CSRW
// and each value lands in the bank word the register package names (masked to
// the bits that exist); CSRW to an unmapped or a runtime address leaves the
// bank alone; CSRW costs 3 cycles for every class-L address (the rule
// registers too) and 1 otherwise; LANE_CFG fields side_en, ss_base (side-set
// wraps modulo 4) and jmp_pin act as named; LANE_PROG.start_pc sets the first pc.
module tb_lane_regs;
`include "lane_h.svh"

    // every class-L lane register address
    localparam int NL = 24;
    logic [7:0] la [NL];
    initial begin
        int k;
        k = 0;
        for (int a = 0; a < 256; a++)
            if (tse_regs_pkg::lane_reg_class(8'(a)) == tse_regs_pkg::RC_L) begin la[k] = 8'(a); k++; end
    end

    function automatic logic [31:0] val(input int a); return 32'h9E37_79B9 * (a + 1) ^ 32'h5A5A_0F0F; endfunction

    // {Y, X} = v in five instructions
    function automatic void put_yx(input logic [31:0] v);
        emit(SET(tse_isa_pkg::SETDST_X, v[23:16])); emit(SET(tse_isa_pkg::SETDST_XH, v[31:24]));
        emit(MOV(tse_isa_pkg::MOVDST_Y, tse_isa_pkg::MOVSRC_X, 0));
        emit(SET(tse_isa_pkg::SETDST_X, v[7:0])); emit(SET(tse_isa_pkg::SETDST_XH, v[15:8]));
    endfunction

    initial begin
        logic [15:0] p [];
        logic [1023:0] prev_cfg;
        int pc_l, pc_r, pc_u, n;
        repeat (3) @(negedge clk); rst_n = 1;
        repeat (2) @(negedge clk);

        // 1. CSRW to every class-L register
        foreach (la[i]) begin put_yx(val(la[i])); emit(CSRW(la[i])); end
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q(); start();
        repeat (1500) @(negedge clk);
        stop();
        n = 0;
        foreach (la[i]) begin
            logic [31:0] m;
            m = tse_regs_pkg::LANE_BANK_MASK[32*tse_regs_pkg::lane_reg_word(la[i]) +: 32];
            if (bank_word(la[i]) !== (val(la[i]) & m)) begin
                errors++; $display("FAIL CSRW 0x%02h: bank word %0d holds %h, want %h", la[i],
                                   tse_regs_pkg::lane_reg_word(la[i]), bank_word(la[i]), val(la[i]) & m);
            end else n++;
        end
        check(n == NL, $sformatf("CSRW reached the named bank word for %0d of %0d class-L registers", n, NL));

        // 2. unmapped and runtime addresses do not touch the bank; costs by class
        pq.delete();
        put_yx(32'hDEAD_BEEF);
        emit(DLY(0));                                           // each CSRW at an even pc: its
        pc_l = pq.size(); emit(CSRW(tse_regs_pkg::LR_RULE_CLK)); // successor is in the same word,
        emit(DLY(0));                                           // so no fetch stall enters the cost
        emit(CSRW(tse_regs_pkg::LR_RULE0_CTL)); emit(DLY(0));
        pc_r = pq.size(); emit(CSRW(tse_regs_pkg::LR_TXCTL)); emit(DLY(0));
        pc_u = pq.size(); emit(CSRW(8'h05)); emit(DLY(0)); emit(CSRW(8'h30));
        emit(DLY(0));
        emit(JMP(tse_isa_pkg::COND_ALWAYS, pq.size()));
        load_q();
        prev_cfg = cfg;
        clear_disp(); start(); repeat (60) @(negedge clk); stop();
        check(at(pc_l + 1) - at(pc_l) == 3 && at(pc_l + 3) - at(pc_l + 2) == 3,
              $sformatf("CSRW RULE_CLK and RULE0_CTL (class L) take %0d and %0d cycles",
                        at(pc_l + 1) - at(pc_l), at(pc_l + 3) - at(pc_l + 2)));
        check(at(pc_r + 1) - at(pc_r) == 1 && at(pc_u + 1) - at(pc_u) == 1 && at(pc_u + 3) - at(pc_u + 2) == 1,
              "CSRW TXCTL (class F) and unmapped 0x05, 0x30 take 1 cycle each");
        prev_cfg[32*tse_regs_pkg::LW_RULE_CLK +: 32] = 32'hDEAD_BEEF & tse_regs_pkg::LR_RULE_CLK_MASK;
        prev_cfg[32*tse_regs_pkg::LW_RULE0_CTL +: 32] = 32'hDEAD_BEEF & tse_regs_pkg::LR_RULE0_CTL_MASK;
        check(cfg === prev_cfg, "only the two class-L writes changed the bank");

        // 3. LANE_CFG: side-set from pin 3 wraps to pin 0; JMP PIN tests jmp_pin
        cfg_set(tse_regs_pkg::LR_LANE_CFG, (1 << tse_regs_pkg::LR_LANE_CFG_SIDE_EN_LO)
                | (3 << tse_regs_pkg::LR_LANE_CFG_SS_BASE_LO) | (2 << tse_regs_pkg::LR_LANE_CFG_JMP_PIN_LO));
        p = new[6];
        p[0] = SET(tse_isa_pkg::SETDST_PINDIRS, 8'hFF);
        p[1] = SS(DLY(20), 1);                                  // pin 3 high, pin 0 low
        p[2] = SS(DLY(20), 2);                                  // pin 3 low, pin 0 high
        p[3] = JMP(tse_isa_pkg::COND_PIN, 5);
        p[4] = SET(tse_isa_pkg::SETDST_PINS, 8'hF2);            // not reached when vin[2] is high
        p[5] = JMP(tse_isa_pkg::COND_ALWAYS, 5);
        load(p); vin = 4'b0100; clear_disp(); start();
        repeat (12) @(negedge clk);
        check(vout === 4'b1000 && voe === 4'b1111, $sformatf("side-set 01 from ss_base 3: pins %b (want 1000)", vout));
        repeat (20) @(negedge clk);
        check(vout === 4'b0001, $sformatf("side-set 10 from ss_base 3: pins %b (want 0001)", vout));
        repeat (20) @(negedge clk);
        check(at(5) >= 0 && at(4) < 0, "JMP PIN taken on jmp_pin 2");
        stop();

        // 4. LANE_PROG.start_pc: the first dispatch is at start_pc
        cfg_set(tse_regs_pkg::LR_LANE_CFG, 0);
        cfg_set(tse_regs_pkg::LR_LANE_PROG, 7 << tse_regs_pkg::LR_LANE_PROG_START_PC_LO);
        pq.delete();
        for (int i = 0; i < 10; i++) emit(JMP(tse_isa_pkg::COND_ALWAYS, i));
        load_q(); clear_disp(); start(); repeat (20) @(negedge clk); stop();
        check(at(7) >= 0 && at(0) < 0, $sformatf("start_pc 7: first dispatch at pc 7 in cycle %0d", at(7)));
        cfg_set(tse_regs_pkg::LR_LANE_PROG, 0);
        finish("tb_lane_regs");
    end
endmodule
