`timescale 1ns/1ps
// Configuration bank with and without its write-data register (WD_REG) side
// by side, latch storage and the lane bank mask, under random writes that
// keep the writer contract (wdata held from the accept cycle until edge 3),
// random write requests while busy, random clears and random read addresses.
// q, rd_data and busy must agree in every cycle.
module tb_cfg_stage;
    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    int errors = 0, writes = 0;

    logic        we, clr, busy_s, busy_d;
    logic [4:0]  a, ra;
    logic [31:0] wd, rd_s, rd_d;
    logic [1023:0] q_s, q_d;
    localparam logic [1023:0] M = tse_regs_pkg::LANE_BANK_MASK;
    tse_cfg_bank #(.LATCH(1), .WD_REG(1), .MASK(M)) b_s (.clk, .rst_n, .clr, .we, .addr(a), .wdata(wd),
        .busy(busy_s), .rd_addr(ra), .rd_data(rd_s), .q(q_s));
    tse_cfg_bank #(.LATCH(1), .WD_REG(0), .MASK(M)) b_d (.clk, .rst_n, .clr, .we, .addr(a), .wdata(wd),
        .busy(busy_d), .rd_addr(ra), .rd_data(rd_d), .q(q_d));

    // hold counts the cycles the writer must still keep wdata after an accept
    int hold;
    always @(negedge clk) if (rst_n) begin
        if (q_s !== q_d || rd_s !== rd_d || busy_s !== busy_d) begin
            errors++;
            if (errors < 10) $display("FAIL mismatch at %0t: busy %b %b rd %h %h", $time, busy_s, busy_d, rd_s, rd_d);
        end
    end

    initial begin
        we = 0; clr = 0; a = 0; ra = 0; wd = 0; hold = 0;
        #22 rst_n = 1;
        for (int c = 0; c < 20000; c++) begin
            @(negedge clk);
            ra = 5'($urandom);
            clr = ($urandom % 500) == 0;
            if (hold > 0) begin
                hold--;
                we = ($urandom % 4) == 0;              // ignored while busy, address may move
                a  = 5'($urandom);
            end else begin
                wd = $urandom;
                a  = 5'($urandom);
                we = ($urandom % 3) == 0;
                if (we && !busy_s) begin hold = 2; writes++; end
            end
        end
        @(negedge clk) we = 0;
        repeat (4) @(negedge clk);
        if (writes < 3000) begin errors++; $display("FAIL only %0d writes", writes); end
        if (errors == 0) $display("PASS tb_cfg_stage (%0d writes)", writes);
        else             $display("FAIL tb_cfg_stage errors=%0d", errors);
        $finish;
    end
endmodule
