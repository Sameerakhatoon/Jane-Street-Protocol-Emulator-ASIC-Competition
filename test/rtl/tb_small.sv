`timescale 1ns/1ps
// Quick checks: event unit trigger, log and drops, half-cycle pin delay taps,
// timer period, cfg bank.
module tb_small;
    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    int errors = 0;

    // event unit
    logic [95:0]  tmatch;
    logic [47:0]  twin;
    logic [16:0]  tctl;
    logic [1:0]   evv;
    logic [11:0]  evc;
    logic [15:0]  evd;
    logic         pop, arm, trig;
    logic [3:0]   tstop;
    logic [5:0]   cflags;
    logic [15:0]  evst;
    logic [31:0]  head, ts;
    tse_event_unit #(.LANES(2), .DEPTH(8)) ev (.clk, .rst_n, .cfg_ctl({5'd0, 4'hF, 1'b1}),
        .cfg_match(tmatch), .cfg_win(twin), .cfg_trig(tctl), .ev_valid(evv), .ev_code(evc), .ev_data(evd),
        .cf_we(3'b0), .cf_mask(12'd0), .cf_val(12'd0), .pop, .clr_fifo(1'b0), .clr_drop(1'b0), .arm,
        .head, .status(evst), .ts, .chip_flags(cflags), .trig_level(trig), .trig_stop(tstop));

    // pin delay
    logic [3:0] dp, dn, op;
    logic [11:0] dcfg;
    tse_pin_delay #(.N(4)) pd (.clk, .rst_n, .cfg_dly(dcfg), .in_p(dp), .in_n(dn), .out_p(op));

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

    task automatic event_on(input int lane, input logic [5:0] code, input logic [7:0] data);
        @(negedge clk); evv = '0; evv[lane] = 1'b1; evc = '0; evc[6*lane +: 6] = code;
        evd = '0; evd[8*lane +: 8] = data;
        @(negedge clk); evv = '0;
    endtask

    task automatic expect_rec(input logic [1:0] lane, input logic [5:0] code, input logic [7:0] data);
        if (evst[3:0] == 4'd0 || head[31:30] != lane || head[29:24] != code || head[23:16] != data) begin
            errors++; $display("FAIL log record %h, want lane %0d code %h data %h", head, lane, code, data);
        end
        @(negedge clk) pop = 1; @(negedge clk) pop = 0;
    endtask

    initial begin
        int t0, period;
        evv = 0; evc = 0; evd = 0; pop = 0; arm = 0; dp = 0; dn = 0; dcfg = {3'd7, 3'd2, 3'd1, 3'd0}; tstart = 0;
        we = 0; clr = 0; a = 0; wd = 0;
        // trigger: lane 1 code 0x2A data 0x5x, then lane 0 code 9 within 20 cycles
        tmatch = '0; twin = '0;
        tmatch[31:0]  = {8'hF0, 8'h50, 6'h3F, 6'h2A, 4'b0010};
        tmatch[63:32] = {8'h00, 8'h00, 6'h3F, 6'h09, 4'b0001};
        twin[31:16]   = 16'd20;
        tctl = {4'b0011, 1'b1, 8'd2, 1'b0, 1'b1, 2'd1};          // stop lanes 0, 1; pulse high 3 cycles
        #22 rst_n = 1;
        event_on(1, 6'h2A, 8'h51); repeat (30) @(negedge clk); event_on(0, 6'h09, 8'h00);   // too late
        repeat (5) @(negedge clk);
        if (trig || evst[13]) begin errors++; $display("FAIL trigger fired outside window"); end
        event_on(1, 6'h2A, 8'h61);                                // data mismatch: no stage advance
        if (evst[15:14] != 2'd0) begin errors++; $display("FAIL stage advanced on data mismatch"); end
        event_on(1, 6'h2A, 8'h5F); repeat (5) @(negedge clk);
        fork
            begin event_on(0, 6'h09, 8'h00); end
            begin
                int hi;
                bit st_seen;
                hi = 0; st_seen = 0;
                repeat (8) begin @(posedge clk); #1 if (trig) hi++; if (tstop == 4'b0011) st_seen = 1; end
                if (hi != 3) begin errors++; $display("FAIL trigger pulse %0d cycles", hi); end
                if (!st_seen) begin errors++; $display("FAIL stop mask pulse missing"); end
            end
        join
        if (!evst[13]) begin errors++; $display("FAIL trigger not fired"); end
        // log: TIME, then the six events in order, the TRIG record before the last lane 0 event
        expect_rec(2'd0, 6'h3F, 8'h00);
        expect_rec(2'd1, 6'h2A, 8'h51);
        expect_rec(2'd0, 6'h09, 8'h00);
        expect_rec(2'd1, 6'h2A, 8'h61);
        expect_rec(2'd1, 6'h2A, 8'h5F);
        expect_rec(2'd0, 6'h3E, 8'h11);
        expect_rec(2'd0, 6'h09, 8'h00);
        if (evst[3:0] != 4'd0 || evst[11:4] != 8'd0) begin errors++; $display("FAIL log not empty or drops %h", evst); end
        @(negedge clk) arm = 1; @(negedge clk) arm = 0;
        if (evst[13] || cflags[4]) begin errors++; $display("FAIL arm did not clear fired and TRIG"); end
        // drops: fill the FIFO from lane 0 without popping
        repeat (12) event_on(0, 6'h01, 8'h00);
        repeat (4) @(negedge clk);
        if (evst[3:0] != 4'd8 || evst[11:4] == 8'd0) begin errors++; $display("FAIL full log status %h", evst); end
        // pin delay: pulses on both inputs; taps 0, 1, 2, 7 half cycles take p, n, p+1, n+3
        begin
            int seen [4];
            seen = '{-1, -1, -1, -1};
            @(negedge clk) begin dp = 4'hF; dn = 4'hF; end
            for (int c = 0; c < 8; c++) begin
                #1;
                for (int i = 0; i < 4; i++) if (op[i] && seen[i] < 0) seen[i] = c;
                @(negedge clk) begin dp = 4'h0; dn = 4'h0; end
            end
            if (seen[0] != 0 || seen[1] != 0 || seen[2] != 1 || seen[3] != 3) begin
                errors++; $display("FAIL delay taps %0d %0d %0d %0d", seen[0], seen[1], seen[2], seen[3]);
            end
            seen = '{-1, -1, -1, -1};
            @(negedge clk) dp = 4'hF;
            for (int c = 0; c < 8; c++) begin
                #1;
                for (int i = 0; i < 4; i++) if (op[i] && seen[i] < 0) seen[i] = c;
                @(negedge clk) dp = 4'h0;
            end
            if (seen[1] != -1 || seen[3] != -1) begin errors++; $display("FAIL odd taps took the rising sample"); end
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
