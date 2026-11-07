// SPDX-License-Identifier: Apache-2.0
`default_nettype none
`timescale 1ns / 1ps

// Wraps the Tiny Tapeout top for cocotb. TT_TOP is the top module name,
// passed in by the Makefile from info.yaml, so a rename never touches this file.
module tb ();

    initial begin
        $dumpfile("tb.fst");
        $dumpvars(0, tb);
        #1;
    end

    reg clk;
    reg rst_n;
    reg ena;
    reg [7:0] ui_in;
    reg [7:0] uio_in;
    wire [7:0] uo_out;
    wire [7:0] uio_out;
    wire [7:0] uio_oe;

    // Icarus cannot trigger on one bit of a vector, so the UART line gets its own net.
    wire uart_tx = uo_out[0];

    `TT_TOP user_project (
        .ui_in  (ui_in),
        .uo_out (uo_out),
        .uio_in (uio_in),
        .uio_out(uio_out),
        .uio_oe (uio_oe),
        .ena    (ena),
        .clk    (clk),
        .rst_n  (rst_n)
    );

endmodule
