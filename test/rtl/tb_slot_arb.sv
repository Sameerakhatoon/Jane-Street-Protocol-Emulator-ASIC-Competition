// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps
// Non-interference: two arbiters see the same lane-0 requests and different
// random traffic from every other lane and the host. Lane 0's grants, valid
// flags and memory access must be identical in both, every grant must fall in
// its own slot, and at most one requester may own the port per cycle.
// all_stopped is held 0: the host owns every cycle only while no lane runs.
`ifndef LANES_TB
`define LANES_TB 2
`endif
module tb_slot_arb;
    localparam int L = `LANES_TB;
    logic clk = 0, rst_n = 0;
    always #5 clk = ~clk;
    int errors = 0, g0 = 0, d0 = 0;

    logic [L-1:0]    req [2], dreq [2], dwe [2], gnt [2], rvalid [2], dgnt [2], drvalid [2];
    logic [L*9-1:0]  addr [2];
    logic [L*7-1:0]  daddr [2];
    logic [L*32-1:0] dwdata [2];
    logic            sys_req [2], sys_we [2], sys_gnt [2], sys_rvalid [2];
    logic [8:0]      sys_addr [2], mem_addr [2];
    logic [31:0]     sys_wdata [2], mem_din [2];
    logic [3:0]      cnt [2];
    logic            men [2], wen [2], ren [2];

    for (genvar c = 0; c < 2; c++) begin : g_copy
        tse_slot_arb #(.LANES(L)) u_arb (
            .clk, .rst_n, .frame_sync(1'b0), .req(req[c]), .addr(addr[c]), .dreq(dreq[c]),
            .dwe(dwe[c]), .daddr(daddr[c]), .dwdata(dwdata[c]), .sys_req(sys_req[c]),
            .sys_we(sys_we[c]), .sys_addr(sys_addr[c]), .sys_wdata(sys_wdata[c]),
            .all_stopped(1'b0), .cnt(cnt[c]), .gnt(gnt[c]), .rvalid(rvalid[c]), .dgnt(dgnt[c]),
            .drvalid(drvalid[c]), .sys_gnt(sys_gnt[c]), .sys_rvalid(sys_rvalid[c]),
            .mem_men(men[c]), .mem_wen(wen[c]), .mem_ren(ren[c]), .mem_addr(mem_addr[c]),
            .mem_din(mem_din[c])
        );
    end

    function automatic int ones(input logic [L:0] v);
        ones = 0;
        for (int i = 0; i <= L; i++) ones += int'(v[i]);
    endfunction

    initial begin
        repeat (2) @(negedge clk);
        rst_n = 1;
        repeat (20000) begin
            for (int c = 0; c < 2; c++) begin
                req[c] = L'($urandom); dreq[c] = L'($urandom); dwe[c] = L'($urandom);
                for (int i = 0; i < L; i++) begin
                    addr[c][9*i +: 9] = 9'($urandom);
                    daddr[c][7*i +: 7] = 7'($urandom);
                    dwdata[c][32*i +: 32] = $urandom;
                end
                sys_req[c] = 1'($urandom); sys_we[c] = 1'($urandom);
                sys_addr[c] = 9'($urandom); sys_wdata[c] = $urandom;
            end
            // lane 0 identical in both copies
            req[1][0] = req[0][0]; dreq[1][0] = dreq[0][0]; dwe[1][0] = dwe[0][0];
            addr[1][8:0] = addr[0][8:0]; daddr[1][6:0] = daddr[0][6:0];
            dwdata[1][31:0] = dwdata[0][31:0];
            #1;
            if (gnt[0][0] !== gnt[1][0] || dgnt[0][0] !== dgnt[1][0]
                || rvalid[0][0] !== rvalid[1][0] || drvalid[0][0] !== drvalid[1][0]) begin
                errors++;
                if (errors < 5) $display("FAIL lane 0 grant differs at cnt %0d", cnt[0]);
            end
            if ((gnt[0][0] || dgnt[0][0])
                && (mem_addr[0] !== mem_addr[1] || wen[0] !== wen[1]
                    || (wen[0] && mem_din[0] !== mem_din[1]))) begin
                errors++;
                if (errors < 5) $display("FAIL lane 0 memory access differs at cnt %0d", cnt[0]);
            end
            for (int c = 0; c < 2; c++) begin
                if (ones({sys_gnt[c], gnt[c] | dgnt[c]}) > 1 || (gnt[c] & dgnt[c]) != '0) begin
                    errors++;
                    if (errors < 5) $display("FAIL two owners at cnt %0d", cnt[c]);
                end
                for (int i = 0; i < L; i++) begin
                    if (gnt[c][i] && cnt[c][1:0] != 2'(i)) begin
                        errors++; $display("FAIL lane %0d fetch outside its slot", i);
                    end
                    if (dgnt[c][i] && (L == 4 || cnt[c] != 4'(4 * i + 3))) begin
                        errors++; $display("FAIL lane %0d data access outside its slot", i);
                    end
                end
                if (sys_gnt[c] && (L == 4 || cnt[c] != 4'd15)) begin
                    errors++; $display("FAIL host access outside slot 15");
                end
            end
            g0 += int'(gnt[0][0]);
            d0 += int'(dgnt[0][0]);
            @(negedge clk);
        end
        if (g0 == 0 || (L < 4 && d0 == 0)) begin errors++; $display("FAIL lane 0 never granted"); end
        if (errors == 0) $display("PASS tb_slot_arb LANES=%0d, lane 0: %0d fetches, %0d data accesses", L, g0, d0);
        else             $display("FAIL tb_slot_arb errors=%0d", errors);
        $finish;
    end
endmodule
