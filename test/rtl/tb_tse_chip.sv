`timescale 1ns/1ps
// Chip-level bring-up test through the real TT pins, as the RP2350 drives them.
// Load mode: loaded clear on entry, BEGIN, BIST, program, lane and chip
// configuration with WR/WRB, COMMIT, STAT, RDB readback, LOAD_CRC.
// Run mode over the SPI target: RUN, a UART byte from lane 0 on uo_out[0]
// (idle high from the mode switch), STATUS, ID, the event log (TIME then
// lane 0 code 5), IRQ read-to-clear, a refused write setting host error.
// Back in load mode loaded reads 0.
`ifndef LANES_TB
`define LANES_TB 3
`endif
`ifndef LATCH_TB
`define LATCH_TB 1
`endif
module tb_tse_chip;
    import tse_regs_pkg::*;

    logic       clk, rst_n;
    logic [7:0] ui_in, uio_in;
    wire  [7:0] uo_out, uio_out, uio_oe;

    tse_chip #(.LANES(`LANES_TB), .CFG_LATCH(`LATCH_TB)) dut (.ui_in, .uo_out, .uio_in, .uio_out, .uio_oe, .ena(1'b1), .clk, .rst_n);

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

    // host address of the lane register stored in bank word w
    function automatic logic [15:0] lane_word_addr(input int lane, input int w);
        for (int a = 0; a < 256; a++)
            if (lane_reg_class(8'(a)) == RC_L && lane_reg_word(8'(a)) == 5'(w))
                return LANE_HOST_BASE + 16'(lane) * LANE_HOST_STRIDE + 16'(a);
        return 16'hFFFF;
    endfunction

    int errors;
    logic [31:0] crc;
    logic [7:0]  rx [$];
    bit          idle_bad;

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

    initial begin
        logic [15:0] p [];
        logic [31:0] pw [], rb [], rd;
        logic [7:0]  s;
        errors = 0;
        ui_in = 8'h00; uio_in = 8'h00; rst_n = 1'b1;
        repeat (4) @(negedge clk);
        rst_n = 1'b0;                                       // load mode: loaded clears
        repeat (8) @(negedge clk);
        ld_stat(s);
        check("STAT on entry, loaded bit", 32'(s[0]), 32'd0);

        ld(8'h10, 1);                                       // BEGIN
        crc = 32'hFFFFFFFF;
        ld(8'h11, 1);                                       // BIST
        repeat (12000) @(negedge clk);
        ld_stat(s);
        check("STAT after BIST", 32'(s), 32'h06);

        p = new[10];
        p[0] = SET(7, 8'hD0);                               // DPLL on, line idle high
        p[1] = SET(2, 9);  p[2] = SET(3, 0);
        p[3] = XFR(2, 0, 8'h21);                            // TXCTL = 9: send 10 bits
        p[4] = SET(2, 8'hAA); p[5] = SET(5, 8'h02);         // X = 0x02AA = {stop, 0x55, start}
        p[6] = XFR(2, 0, 8'h20);                            // TXDATA = {Y, X}
        p[7] = WAITI(1, 1, 6, 0, 0, 0);                     // wait TX idle
        p[8] = SET(6, 5);                                   // event code 5
        p[9] = JMP(0, 9);
        pw = new[5];
        foreach (pw[i]) pw[i] = {p[2*i + 1], p[2*i]};
        ld_wrb(16'h0000, pw);
        // lane 0 words 5 to 9: no stuffing, NRZ; 64 cycles per bit; TX owns vpin 0, idles high
        ld_wr(lane_word_addr(0, 5), 32'd0);
        ld_wr(lane_word_addr(0, 6), 32'd1024);
        ld_wr(lane_word_addr(0, 7), 32'd512);
        ld_wr(lane_word_addr(0, 8), 32'd0);
        ld_wr(lane_word_addr(0, 9), (1 << 19) | (1 << 25));
        ld_wr(LANE_HOST_BASE + 16'(LR_LANE_PROG), 32'd0);   // window 0, start 0
        ld_wr(HR_PAD_OUT, 32'h1C0);                         // uo_out[0] <- lane 0 vpin 0: en, idle 1, idle_oe
        ld_wr(HR_EV_CTL, 32'h3);                            // log lane 0

        ld(8'h12, 1);                                       // COMMIT
        for (int k = 3; k >= 0; k--) ld(8'(~crc >> (8 * k)), 0);
        ld_stat(s);
        check("STAT after COMMIT", 32'(s), 32'h07);

        ld_rdb(16'h0000, 5, rb);
        foreach (pw[i]) if (rb[i] !== pw[i]) begin errors++; $display("FAIL readback word %0d %h %h", i, rb[i], pw[i]); end
        ld_rdb(HR_LOAD_CRC, 1, rb);
        check("LOAD_CRC", rb[0], ~crc);
        ld_rdb(lane_word_addr(0, 9), 1, rb);
        check("lane 0 word 9 readback", rb[0], (1 << 19) | (1 << 25));
        ld_rdb(HR_PAD_OUT, 1, rb);
        check("PAD_OUT[0] readback", rb[0], 32'h1C0);

        fork                                                // run mode: decode the UART byte
            begin
                logic [7:0] b;
                int hi;
                @(posedge rst_n);
                repeat (3) @(posedge clk);                  // uo_out switches to the pads with load mode
                hi = 0;
                while (uo_out[0] === 1'b1) begin @(posedge clk); hi++; end
                idle_bad = hi < 500;                       // the lane starts only after the SPI RUN write
                $display("info uo_out[0] high for %0d cycles before the start bit", hi);
                repeat (32) @(posedge clk);
                for (int i = 0; i < 10; i++) begin
                    if (i >= 1 && i <= 8) b[i - 1] = uo_out[0];
                    repeat (64) @(posedge clk);
                end
                rx.push_back(b);
            end
            begin
                ui_in = 8'h01;                              // SPI CS high, SCK low
                @(negedge clk) rst_n = 1'b1;
                repeat (20) @(negedge clk);
                spi_wr(HR_RUN, 32'h1);
                repeat (1500) @(negedge clk);
            end
        join
        if (rx.size() != 1 || rx[0] !== 8'h55) begin errors++; $display("FAIL UART got %p", rx); end
        else $display("info lane 0 sent 0x55 on uo_out[0]");
        if (idle_bad) begin errors++; $display("FAIL uo_out[0] not idle high before the start bit"); end

        spi_rd(HR_STATUS, rd);
        check("STATUS", rd, 32'h107);
        spi_rd(HR_ID, rd);
        check("ID", rd, {12'd0, 4'd4, 1'(`LATCH_TB), 3'(`LANES_TB), 4'd1, 8'h54});
        spi_rd(HR_EV_POP, rd);
        check("first record is TIME, epoch 0", rd, 32'h3F00_0000);
        spi_rd(HR_EV_POP, rd);
        check("lane 0 event code 5", {rd[31:16], 16'd0}, 32'h0500_0000);
        spi_rd(HR_IRQ, rd);
        check("IRQ", rd & 32'hF, 32'h5);
        spi_rd(HR_IRQ, rd);
        check("IRQ after the clearing read", rd, 32'h0);
        spi_wr(lane_word_addr(0, 9), 32'd0);                // running lane: refused
        spi_rd(HR_STATUS, rd);
        check("STATUS after a refused write", rd, 32'h10F);
        spi_rd(lane_word_addr(0, 9), rd);
        check("refused write left the word", rd, (1 << 19) | (1 << 25));

        rst_n = 1'b0;                                       // load mode clears loaded
        repeat (8) @(negedge clk);
        ld_stat(s);
        check("STAT after re-entering load mode, loaded bit", 32'(s[0]), 32'd0);

        if (errors == 0) $display("PASS tb_tse_chip");
        else             $display("FAIL tb_tse_chip errors=%0d", errors);
        $finish;
    end
endmodule
