`timescale 1ns/1ps
// Sequencer and response rule checks: two-entry fetch buffer, WAIT alternate
// condition, CSRW config cost, response rules in pin-edge mode with a tick
// delay and in bit mode with an offset (the drive register
// changes at the end of the cycle tick_delay after the counted edge, so it is
// seen tick_delay + 1 cycles later), 8-bit counter saturation.
module tb_consol;
    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;   // posedges at 5, 15, ...: $time/10 is the cycle index
    int errors = 0;
    longint cyc = 0;
    always @(posedge clk) cyc = $time / 10;   // cycle number, race-free

    function automatic logic [15:0] SET(input int d, v);   // PINS, PINDIRS: all four pins (mask 1111)
        return {3'd2, 2'b0, 3'(d), 8'(d < 2 ? 8'hF0 | v : v)};
    endfunction
    function automatic logic [15:0] JMP(input int c, t);  return {3'd0, 2'b0, 3'(c), 8'(t)}; endfunction
    function automatic logic [15:0] DLY(input int d);     return {3'd7, 2'b0, 2'b0, 9'(d)}; endfunction
    function automatic logic [15:0] CSRW(input int a);    return {3'd6, 2'b0, 2'd2, 1'b0, 8'(a)}; endfunction
    function automatic logic [15:0] WAITI(input int pol, src, idx, to, alt, d);
        return {3'd1, 2'b0, 1'(pol), 2'(src), 3'(idx), 1'(to), 1'(alt), 3'(d)};
    endfunction

    // ---------------- sequencer with a slot-gated memory (this lane = slot 0 of 4)
    logic        run, req, gnt;
    logic [6:0]  addr;
    logic [31:0] rdata;
    logic [31:0] mem [128];
    logic [1:0]  slot;
    logic [3:0]  pin_in, pout, poe;
    logic        csr_we;
    int          grants;
    always_ff @(posedge clk) begin
        slot <= rst_n ? slot + 2'd1 : 2'd0;
        if (gnt) rdata <= mem[addr];
        if (gnt) grants <= grants + 1;
    end
    assign gnt = req && slot == 2'd0;

    tse_seq dut (.clk, .rst_n, .run, .start_pc(8'd0), .chip_flags(6'd0), .gt(64'd0), .dmem_gnt(1'b0), .dmem_rdata(32'd0),
        .imem_req(req), .imem_addr(addr), .imem_gnt(gnt), .imem_rdata(rdata),
        .alt_cfg(6'b001_00_1), .flg(8'h80),
        .pin_in, .pin_out(pout), .pin_oe(poe), .side_en(1'b0), .ss_base(2'd0), .jmp_pin(2'd0), .ev(8'd0),
        .csr_we, .csr_re(), .csr_addr(), .csr_wdata(), .csr_rdata(32'd0), .cmd_we(), .cmd(),
        .h_tx_we(1'b0), .h_tx_data(32'd0), .h_tx_full(), .h_rx_re(1'b0), .h_rx_data(), .h_rx_full(), .irq(), .aflag_o(), .tflag_o());

    task automatic load(input logic [15:0] p []);
        foreach (mem[i]) mem[i] = {SET(0, 0), JMP(0, 0)};
        for (int i = 0; i < p.size(); i += 2) mem[i / 2] = {(i + 1 < p.size()) ? p[i + 1] : 16'h0, p[i]};
    endtask

    task automatic restart();
        run = 0; repeat (3) @(posedge clk); grants = 0; run = 1;
    endtask

    // ---------------- response rule, pin-edge mode, I2C-style
    logic scl = 1, sda = 1, frst = 0, drv_en, drv_val;
    logic [1:0] hits;
    tse_resp_rule #(.K(2), .PW(16)) rr (.clk, .rst_n,
        .cfg_pat({16'h0, 16'h00A0}), .cfg_msk({16'h0, 16'h00FF}), .cfg_pos({9'd0, 9'd7}),
        .cfg_resp({16'h0, 16'h0000}), .cfg_ctl({14'h0, 14'h1400}),
        .cfg_src(1'b1), .cfg_edge(1'b1), .cfg_tdly(8'd18), .cfg_hold(1'b0), .seq_wr(1'b0), .frame_rst(frst),
        .bit_valid(1'b0), .bit_in(1'b0), .bit_start(1'b0), .pin_clk(scl), .pin_dat(sda),
        .crc_ok(1'b0), .ext_flag(1'b0), .flags_clr(frst), .drv_en, .drv_val, .hit_flags(hits),
        .hit_pulse(), .abort_pulse());

    logic frst2 = 0, bv2 = 0, bin2 = 0, bst2 = 0, d2en;
    tse_resp_rule #(.K(2), .PW(16)) rr2 (.clk, .rst_n,
        .cfg_pat({16'h0, 16'h0005}), .cfg_msk({16'h0, 16'h000F}), .cfg_pos({9'd0, 9'd3}),
        .cfg_resp({16'h0, 16'h0001}), .cfg_ctl({14'h0, 14'h1420}),
        .cfg_src(1'b0), .cfg_edge(1'b0), .cfg_tdly(8'd0), .cfg_hold(1'b0), .seq_wr(1'b0), .frame_rst(frst2),
        .bit_valid(bv2), .bit_in(bin2), .bit_start(bst2), .pin_clk(1'b0), .pin_dat(1'b0),
        .crc_ok(1'b0), .ext_flag(1'b0), .flags_clr(frst2), .drv_en(d2en), .drv_val(), .hit_flags(),
        .hit_pulse(), .abort_pulse());

    longint fall_t [$], drv_on, drv_off;
    always @(posedge clk) if (rst_n && rr.cedge) fall_t.push_back(($time / 10));
    logic drv_q = 0;
    always @(posedge clk) begin
        drv_q <= drv_en;
        if (drv_en && !drv_q) drv_on = ($time / 10);
        if (!drv_en && drv_q) drv_off = ($time / 10);
    end

    task automatic i2c_byte(input logic [7:0] b);   // SCL period 100 cycles, SDA mid-low
        for (int i = 7; i >= 0; i--) begin
            repeat (25) @(negedge clk); sda = b[i];
            repeat (25) @(negedge clk); scl = 1;
            repeat (50) @(negedge clk); scl = 0;
        end
        repeat (25) @(negedge clk); sda = 1;            // master releases for the ACK clock
        repeat (25) @(negedge clk); scl = 1;
        repeat (50) @(negedge clk); scl = 0;
        repeat (60) @(negedge clk);
    endtask

    initial begin
        longint t0, rise [$], hi;
        logic [15:0] p [];
        run = 0; pin_in = 0; grants = 0;
        repeat (3) @(posedge clk); rst_n = 1;

        // fetch buffer: a 3-instruction loop across two words runs from the buffer
        p = new[5];
        p[0] = SET(2, 20); p[1] = SET(0, 1); p[2] = SET(0, 0); p[3] = JMP(2, 1); p[4] = JMP(0, 4);
        load(p); restart();
        rise.delete();
        begin
            logic q; int g0, g1;
            q = 0; g0 = -1; g1 = -1;
            repeat (80) begin
                @(posedge clk);
                if (pout[0] && !q) begin rise.push_back(($time / 10)); if (rise.size() == 3) g0 = grants; if (rise.size() == 8) g1 = grants; end
                q = pout[0];
            end
            if (rise.size() < 8) begin errors++; $display("FAIL fetch buffer loop: %0d pulses", rise.size()); end
            else begin
                bit ok; ok = 1;
                for (int i = 2; i < 8; i++) if (rise[i] - rise[i - 1] != 3) ok = 0;
                if (!ok) begin errors++; $display("FAIL fetch buffer loop period not 3: %p", rise); end
                else $display("info fetch buffer two-word loop: period 3 cycles, %0d fetches in five iterations", g1 - g0);
            end
        end

        // fetch buffer: successor in the next word after a WAIT, for the 4 phases of the input
        // against the slots: the stall is 0 to 3 cycles (handover 1 to 4)
        p = new[4];
        p[0] = SET(0, 1); p[1] = WAITI(1, 0, 0, 0, 0, 0); p[2] = SET(0, 0); p[3] = JMP(0, 3);
        load(p);
        begin
            int gaps [$];
            for (int ph = 0; ph < 4; ph++) begin
                longint tw, ts;
                pin_in = 0; restart(); repeat (20 + ph) @(negedge clk);
                pin_in = 4'h1; tw = -1; ts = -1;
                repeat (12) begin
                    @(posedge clk);
                    if (dut.retire && dut.ic == tse_isa_pkg::I_WAIT) tw = ($time / 10);
                    if (dut.retire && dut.ic == tse_isa_pkg::I_SET && tw >= 0 && ts < 0) ts = ($time / 10);
                end
                gaps.push_back(int'(ts - tw));
            end
            $display("info fetch buffer WAIT to next-word successor: %p cycles over the 4 phases", gaps);
            foreach (gaps[i]) if (gaps[i] < 1 || gaps[i] > 4) begin errors++; $display("FAIL fetch buffer handover"); break; end
        end

        // WAIT alternate: WAIT pin0 OR second condition (pin1 high); TFLAG tells which
        p = new[6];
        p[0] = WAITI(1, 0, 0, 0, 1, 0); p[1] = JMP(6, 4); p[2] = SET(0, 1); p[3] = JMP(0, 3);
        p[4] = SET(0, 2); p[5] = JMP(0, 5);
        load(p);
        for (int which = 0; which < 2; which++) begin
            longint tr;
            pin_in = 0; restart(); repeat (20) @(posedge clk);
            @(negedge clk) pin_in = which ? 4'h1 : 4'h2;
            tr = -1;
            repeat (20) begin @(posedge clk); if (tr < 0 && dut.retire && dut.ic == tse_isa_pkg::I_WAIT) tr = ($time / 10); end
            if (pout[1:0] != (which ? 2'b01 : 2'b10)) begin errors++; $display("FAIL WAIT alternate cond %0d pins %b", which, pout); end
            else $display("info WAIT alternate %s ended the WAIT, JMP TOUT %s, WAIT retired in the first cycle it saw the pin: %0d",
                                        which ? "own condition" : "alternate condition", which ? "not taken" : "taken (AFLAG)",
                                        tr >= 0);
        end

        // CSRW cost: CSRW to a config word (here a rule register) lasts 3 cycles, to a lane register 1
        p = new[5];
        p[0] = CSRW(tse_regs_pkg::LR_RULE0_RESP_POS); p[1] = SET(0, 1); p[2] = CSRW(8'h21); p[3] = SET(0, 0); p[4] = JMP(0, 4);
        load(p);
        restart();
        begin
            longint tw [$];
            repeat (40) begin @(posedge clk); if (dut.retire) tw.push_back(($time / 10)); end
            if (tw[1] - tw[0] != 3 || tw[3] - tw[2] != 1) begin errors++; $display("FAIL CSRW cost %p", tw); end
            else $display("info CSRW cost: config word 3 cycles, lane register 1 cycle");
        end

        // pin-edge rule, I2C address ACK: SCL falling edges counted, address 0xA0 matches
        // at bit 7, offset 0: ACK driven 18 + 1 ticks after the 8th fall, released after the 9th
        frst = 1; @(negedge clk); frst = 0; fall_t.delete();
        scl = 1; sda = 1; repeat (10) @(negedge clk); sda = 0; repeat (25) @(negedge clk); scl = 0;  // START
        @(negedge clk) frst = 1; @(negedge clk) frst = 0; fall_t.delete();
        i2c_byte(8'hA0);
        if (fall_t.size() != 9) begin errors++; $display("FAIL pin-edge rule %0d SCL falls", fall_t.size()); end
        else if (drv_on - fall_t[7] != 19 || drv_off - fall_t[8] != 19 || drv_val !== 1'b0)
            begin errors++; $display("FAIL pin-edge rule ACK at +%0d, release at +%0d", drv_on - fall_t[7], drv_off - fall_t[8]); end
        else $display("info pin-edge rule ACK driven %0d ticks (tdly 18 + 1) after the 8th SCL fall, released %0d after the 9th",
                                    drv_on - fall_t[7], drv_off - fall_t[8]);
        drv_on = 0;
        @(negedge clk) frst = 1; @(negedge clk) frst = 0;
        i2c_byte(8'hA2);
        if (drv_on != 0) begin errors++; $display("FAIL pin-edge rule drove for another address"); end
        else $display("info pin-edge rule no ACK for address 0xA2");

        // bit mode: pattern 0101 at bit 3, offset 2: drive from the 2nd BITSTART after the match (BITSTART 5)
        begin
            logic [7:0] bits;
            int on_at;
            bits = 8'b0000_1010; on_at = -1;
            @(negedge clk) frst2 = 1; @(negedge clk) frst2 = 0;
            for (int i = 0; i < 8; i++) begin
                @(negedge clk) bst2 = 1; @(negedge clk) bst2 = 0;
                if (d2en && on_at < 0) on_at = i;
                repeat (8) @(negedge clk);
                bin2 = bits[i]; bv2 = 1; @(negedge clk) bv2 = 0;
                repeat (8) @(negedge clk);
            end
            if (on_at != 5) begin errors++; $display("FAIL bit mode drive at BITSTART %0d", on_at); end
            else $display("info bit mode: match at bit 3, drive from BITSTART 5 (the 2nd after the match)");
        end

        // saturation: 3000 edges, counter stops at 255
        @(negedge clk) frst = 1; @(negedge clk) frst = 0;
        repeat (3000) begin @(negedge clk) scl = 1; @(negedge clk) scl = 0; end
        if (rr.cnt != 8'hff) begin errors++; $display("FAIL counter %h", rr.cnt); end
        else $display("info rule counter saturates at 255");

        if (errors == 0) $display("PASS tb_consol");
        else             $display("FAIL tb_consol errors=%0d", errors);
        $finish;
    end
endmodule
