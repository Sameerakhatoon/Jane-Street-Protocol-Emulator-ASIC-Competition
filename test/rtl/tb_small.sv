`timescale 1ns/1ps
// Quick checks: event unit trigger and log, pin delay taps, timer period, cfg bank.
module tb_small;
    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    int errors = 0;

    // event unit
    logic [127:0] stage;
    logic [2:0]   evv;
    logic [11:0]  evc;
    logic         pop, empty, ovf, trig;
    logic [31:0]  head, ts, tts;
    tse_event_unit #(.LANES(3), .DEPTH(8), .STAGES(4)) ev (.clk, .rst_n, .run(1'b1), .cfg_stage(stage),
        .cfg_trig_len(8'd3), .ev_valid(evv), .ev_code(evc), .pop, .head, .empty, .overflow(ovf),
        .ts, .trig_ts(tts), .trig_out(trig));

    // pin delay
    logic [3:0] dp, dn, op, on;
    logic [11:0] dcfg;
    tse_pin_delay #(.N(4), .D(8)) pd (.clk, .rst_n, .cfg_dly(dcfg), .in_p(dp), .in_n(dn), .out_p(op), .out_n(on));

    // timer
    logic tick, exp_, tstart;
    logic [15:0] cnt;
    tse_timer tm (.clk, .rst_n, .cfg_inc(24'h400000), .cfg_reload(16'd3), .cfg_on_tick(1'b1),
        .cfg_auto(1'b1), .en(1'b1), .start(tstart), .tick, .expired(exp_), .count(cnt));

    // cfg bank, latch and flop
    logic we, clr, busy0, busy1;
    logic [4:0] a;
    logic [31:0] wd, rd0, rd1;
    logic [1023:0] q0, q1;
    tse_cfg_bank #(.LATCH(1)) b0 (.clk, .rst_n, .clr, .we, .addr(a), .wdata(wd), .busy(busy0), .rd_addr(a), .rd_data(rd0), .q(q0));
    tse_cfg_bank #(.LATCH(0)) b1 (.clk, .rst_n, .clr, .we, .addr(a), .wdata(wd), .busy(busy1), .rd_addr(a), .rd_data(rd1), .q(q1));

    task automatic event_on(input int lane, input logic [3:0] code);
        @(negedge clk); evv = '0; evv[lane] = 1'b1; evc = '0; evc[4*lane +: 4] = code;
        @(negedge clk); evv = '0;
    endtask

    initial begin
        int t0, period;
        evv = 0; evc = 0; pop = 0; dp = 0; dn = 0; dcfg = {3'd7, 3'd3, 3'd1, 3'd0}; tstart = 0;
        we = 0; clr = 0; a = 0; wd = 0;
        // trigger: lane 1 code 2, then lane 2 code 9 within 20 cycles
        stage = '0;
        stage[31:0]  = {1'b1, 3'd0, 4'b0010, 4'hF, 4'd2, 16'd0};
        stage[63:32] = {1'b1, 3'd0, 4'b0100, 4'hF, 4'd9, 16'd20};
        #22 rst_n = 1;
        event_on(1, 2); repeat (30) @(negedge clk); event_on(2, 9);       // too late: no trigger
        repeat (5) @(negedge clk);
        if (trig) begin errors++; $display("FAIL trigger fired outside window"); end
        event_on(1, 2); repeat (5) @(negedge clk); event_on(2, 9);        // inside window
        repeat (2) @(negedge clk);
        if (!trig) begin errors++; $display("FAIL trigger missing"); end
        // log holds 4 entries: lanes 1,2,1,2
        for (int i = 0; i < 4; i++) begin
            logic [1:0] expect_lane;
            expect_lane = (i % 2 == 0) ? 2'd1 : 2'd2;
            if (empty || head[31:30] != expect_lane) begin errors++; $display("FAIL log %0d %h", i, head); end
            @(negedge clk) pop = 1; @(negedge clk) pop = 0;
        end
        if (!empty || ovf) begin errors++; $display("FAIL log not empty or overflow"); end
        // pin delay: a pulse on all inputs appears 0, 1, 3, 7 cycles later
        begin
            int seen [4];
            @(negedge clk) dp = 4'hF;
            for (int c = 0; c < 10; c++) begin
                #1;
                for (int i = 0; i < 4; i++) if (op[i]) seen[i] = c;
                @(negedge clk) dp = 4'h0;
            end
            if (seen[0] != 0 || seen[1] != 1 || seen[2] != 3 || seen[3] != 7) begin
                errors++; $display("FAIL delay taps %0d %0d %0d %0d", seen[0], seen[1], seen[2], seen[3]);
            end
        end
        // timer: inc = 2^22 -> a tick every 4 cycles; reload 3 -> expired every 12 cycles
        @(negedge clk) tstart = 1; @(negedge clk) tstart = 0;
        @(posedge exp_); t0 = $time; @(posedge exp_); period = ($time - t0) / 10;
        if (period != 12) begin errors++; $display("FAIL timer period %0d", period); end
        // cfg bank: three-cycle writes, both variants agree, clear works
        for (int i = 0; i < 32; i++) begin
            @(negedge clk); we = 1; a = 5'(i); wd = 32'hA5000000 | i;
            @(negedge clk); we = 0;
            @(negedge clk);
        end
        repeat (2) @(negedge clk);
        for (int i = 0; i < 32; i++) begin
            a = 5'(i); #1;
            if (rd0 !== (32'hA5000000 | i) || rd1 !== rd0) begin errors++; $display("FAIL bank word %0d %h %h", i, rd0, rd1); end
        end
        @(negedge clk) clr = 1; @(negedge clk) clr = 0; repeat (2) @(negedge clk);
        if (q0 !== '0 || q1 !== '0) begin errors++; $display("FAIL bank clear"); end
        if (errors == 0) $display("PASS tb_small");
        else             $display("FAIL tb_small errors=%0d", errors);
        $finish;
    end
endmodule
