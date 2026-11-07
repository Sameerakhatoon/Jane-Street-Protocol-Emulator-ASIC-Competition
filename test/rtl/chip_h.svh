// SPDX-License-Identifier: Apache-2.0
// Whole-chip test harness, included inside a testbench module: the chip
// through its Tiny Tapeout pins at 60 MHz, instruction encoders, the load-mode
// byte bus (WR, WRB, RDB, STAT) with the running load CRC, the run-mode SPI
// target, and check(). The testbench sets LATCH_TB for the configuration kind.
`ifndef LATCH_TB
`define LATCH_TB 1
`endif
logic       clk, rst_n;
logic [7:0] ui_in, uio_in;
wire  [7:0] uo_out, uio_out, uio_oe;

tse_chip #(.CFG_LATCH(`LATCH_TB)) dut (.ui_in, .uo_out, .uio_in, .uio_out, .uio_oe, .ena(1'b1), .clk, .rst_n);

initial clk = 1'b0;
always #8.333 clk = ~clk;

function automatic logic [15:0] SET(input int d, v);        return {3'd2, 2'b0, 3'(d), 8'(v)}; endfunction
function automatic logic [15:0] XFR(input int sub, blk, a); return {3'd6, 2'b0, 2'(sub), 1'(blk), 8'(a)}; endfunction
function automatic logic [15:0] WAITI(input int pol, src, idx, to, alt, d);
    return {3'd1, 2'b0, 1'(pol), 2'(src), 3'(idx), 1'(to), 1'(alt), 3'(d)};
endfunction
function automatic logic [15:0] JMP(input int c, t);        return {3'd0, 2'b0, 3'(c), 8'(t)}; endfunction

function automatic logic [31:0] crc32_byte(input logic [31:0] c, input logic [7:0] b);
    c = c ^ {24'h0, b};
    for (int i = 0; i < 8; i++) c = c[0] ? ((c >> 1) ^ 32'hEDB88320) : (c >> 1);
    return c;
endfunction

int errors;
logic [31:0] crc;

// one byte on the load bus: data and frame first, then a strobe edge
task automatic ld(input logic [7:0] b, input bit frame);
    @(negedge clk) begin ui_in = b; uio_in[1] = frame; end
    @(negedge clk) uio_in[0] = ~uio_in[0];
    repeat (6) @(negedge clk);
endtask
task automatic ld_wr(input logic [15:0] addr, input logic [31:0] w);
    ld(8'h01, 1);
    ld(addr[15:8], 0); crc = crc32_byte(crc, addr[15:8]);
    ld(addr[7:0], 0);  crc = crc32_byte(crc, addr[7:0]);
    for (int k = 3; k >= 0; k--) begin ld(w[8*k +: 8], 0); crc = crc32_byte(crc, w[8*k +: 8]); end
endtask
task automatic ld_wrb(input logic [15:0] addr, input logic [31:0] w []);
    ld(8'h03, 1);
    ld(addr[15:8], 0); crc = crc32_byte(crc, addr[15:8]);
    ld(addr[7:0], 0);  crc = crc32_byte(crc, addr[7:0]);
    ld(8'(w.size() >> 8), 0); ld(8'(w.size()), 0);
    foreach (w[i]) begin
        logic [31:0] x;
        x = w[i];
        for (int k = 3; k >= 0; k--) begin ld(x[8*k +: 8], 0); crc = crc32_byte(crc, x[8*k +: 8]); end
    end
endtask
task automatic ld_rdb(input logic [15:0] addr, input int n, output logic [31:0] r []);
    r = new[n];
    ld(8'h04, 1); ld(addr[15:8], 0); ld(addr[7:0], 0); ld(8'(n >> 8), 0); ld(8'(n), 0);
    for (int i = 0; i < n; i++) begin
        logic [31:0] w;
        for (int k = 3; k >= 0; k--) begin ld(8'h00, 0); w[8*k +: 8] = uo_out; end
        r[i] = w;
    end
endtask
task automatic ld_stat(output logic [7:0] s);
    ld(8'h13, 1); s = uo_out;
endtask

// runtime SPI, mode 0, SCK = clk / 16; n bytes, first byte in bits 63:56
task automatic spi(input logic [63:0] tx, input int n, output logic [63:0] rxv);
    ui_in[0] = 1'b0;
    repeat (16) @(negedge clk);
    for (int i = 63; i >= 64 - 8 * n; i--) begin
        ui_in[2] = tx[i];
        repeat (8) @(negedge clk);
        ui_in[1] = 1'b1;
        rxv[i] = uo_out[3];
        repeat (8) @(negedge clk);
        ui_in[1] = 1'b0;
    end
    repeat (8) @(negedge clk);
    ui_in[0] = 1'b1;
    repeat (16) @(negedge clk);
endtask
task automatic spi_wr(input logic [15:0] a, input logic [31:0] d);
    logic [63:0] r;
    spi({8'h01, a, d, 8'h00}, 7, r);
endtask
task automatic spi_rd(input logic [15:0] a, output logic [31:0] d);
    logic [63:0] r;
    spi({8'h02, a, 8'h00, 32'h0}, 8, r);
    d = r[31:0];
endtask

task automatic check(input string what, input logic [31:0] got, want);
    if (got !== want) begin errors++; $display("FAIL %s: got %h want %h", what, got, want); end
    else $display("info %s = %h", what, got);
endtask
