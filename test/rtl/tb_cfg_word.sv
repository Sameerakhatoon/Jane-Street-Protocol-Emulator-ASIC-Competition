// SPDX-License-Identifier: Apache-2.0
`timescale 1ns/1ps
// Latch and flop versions of tse_cfg_word side by side under random writes
// that obey the writer contract; both must agree in the middle of every cycle
// and match a model that takes the value at the write edge.
module tb_cfg_word;
    logic clk = 0, clr_n = 0, we = 0;
    logic [31:0] wdata = '0, q_lat, q_ff, model;
    always #5 clk = ~clk;
    int errors = 0, writes = 0, hold = 0;

    tse_cfg_word #(.LATCH(1)) u_lat (.clk, .clr_n, .we, .wdata, .q(q_lat));
    tse_cfg_word #(.LATCH(0)) u_ff  (.clk, .clr_n, .we, .wdata, .q(q_ff));

    // Drive on the falling edge, check on the next falling edge (mid-cycle
    // for the posedge logic).
    initial begin
        model = '0;
        repeat (2) @(negedge clk);
        clr_n = 1;
        repeat (2000) begin
            @(negedge clk);
            if (we) model = wdata;
            if (q_lat !== model || q_ff !== model) begin
                errors++;
                if (errors < 5) $display("FAIL t=%0t lat=%h ff=%h model=%h", $time, q_lat, q_ff, model);
            end
            we = 0;
            if (hold > 0) hold--;
            else if ($urandom_range(3) == 0) begin
                we = 1; wdata = $urandom; hold = 2 + $urandom_range(2); writes++;
            end
        end
        @(negedge clk);
        clr_n = 0;
        @(negedge clk);
        if (q_lat !== '0 || q_ff !== '0) begin errors++; $display("FAIL clear"); end
        if (errors == 0) $display("PASS tb_cfg_word %0d writes, latch and flop agree mid-cycle", writes);
        else             $display("FAIL tb_cfg_word errors=%0d", errors);
        $finish;
    end
endmodule
