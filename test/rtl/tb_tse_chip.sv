`timescale 1ns/1ps
// Chip-level bring-up test through the real TT pins:
// load mode: self-test, CLEAR, write program and config, CRC check, read
// back, COMMIT; run mode: lane 0 sends a UART byte on uo_out[0] and logs an
// event; the host reads status and the event over the runtime SPI.
`ifndef LANES_TB
`define LANES_TB 3
`endif
`ifndef LATCH_TB
`define LATCH_TB 1
`endif
module tb_tse_chip;
    logic       clk, rst_n;
    logic [7:0] ui_in, uio_in;
    wire  [7:0] uo_out, uio_out, uio_oe;

    tse_chip #(.LANES(`LANES_TB), .CFG_LATCH(`LATCH_TB)) dut (.ui_in, .uo_out, .uio_in, .uio_out, .uio_oe, .ena(1'b1), .clk, .rst_n);

    initial clk = 1'b0;
    always #8.333 clk = ~clk;

    function automatic logic [15:0] SET(input int d, v);        return {3'd2, 2'b0, 3'(d), 8'(v)}; endfunction
    function automatic logic [15:0] MOVI(input int d, s, dl);   return {3'd3, 2'b0, 3'(d), 3'(s), 5'(dl)}; endfunction
    function automatic logic [15:0] XFR(input int sub, blk, a); return {3'd6, 2'b0, 2'(sub), 1'(blk), 8'(a)}; endfunction
    function automatic logic [15:0] WAITI(input int pol, src, idx, to, alt, d);
        return {3'd1, 2'b0, 1'(pol), 2'(src), 3'(idx), 1'(to), 1'(alt), 3'(d)};
    endfunction
    function automatic logic [15:0] JMP(input int c, t);        return {3'd0, 2'b0, 3'(c), 8'(t)}; endfunction

    int errors;
    bit idle_bad;
    logic [15:0] crc;
    logic [7:0]  rx [$];

    function automatic logic [15:0] crc16(input logic [15:0] c, input logic [7:0] d);
        c = c ^ {d, 8'h00};
        for (int i = 0; i < 8; i++) c = c[15] ? {c[14:0], 1'b0} ^ 16'h1021 : {c[14:0], 1'b0};
        return c;
    endfunction

    // one byte per clock, changed on the falling edge like the RP2350 PIO
    task automatic put(input logic [7:0] b);
        @(negedge clk) ui_in = b;
    endtask
    task automatic idle(input int n);
        repeat (n) put(8'h00);
    endtask
    task automatic write_words(input logic [15:0] addr, input logic [31:0] w []);
        put(8'hA5); put(8'h01); put(addr[15:8]); put(addr[7:0]); put(8'(w.size() - 1));
        foreach (w[i]) begin
            logic [31:0] x;
            x = w[i];
            for (int k = 3; k >= 0; k--) begin put(x[8*k +: 8]); crc = crc16(crc, x[8*k +: 8]); end
        end
        idle(4);
    endtask

    // runtime SPI, mode 0, SCK = clk / 16
    task automatic spi_xfer(input logic [7:0] cmd, input logic [15:0] addr,
                                                    input logic [31:0] wdata, output logic [31:0] rdata);
        logic [55:0] o;
        o = {cmd, addr, wdata};
        rdata = '0;
        ui_in[0] = 1'b0;
        repeat (16) @(negedge clk);
        for (int i = 55; i >= 0; i--) begin
            ui_in[2] = o[i];
            repeat (8) @(negedge clk);
            ui_in[1] = 1'b1;
            if (i < 32) rdata[i] = uo_out[3];
            repeat (8) @(negedge clk);
            ui_in[1] = 1'b0;
        end
        repeat (8) @(negedge clk);
        ui_in[0] = 1'b1;
        repeat (16) @(negedge clk);
    endtask

    initial begin
        logic [15:0] p [];
        logic [31:0] pw [], lw [], cw [], rd;
        logic [7:0]  seen [$];
        int lat;
        errors = 0; crc = 16'hffff;
        ui_in = 8'h00; uio_in = 8'h00; rst_n = 1'b1;
        repeat (4) @(negedge clk);
        rst_n = 1'b0;                                       // load mode
        idle(8);
        if (uo_out[2:0] != 3'b101) begin errors++; $display("FAIL loader not alive"); end

        put(8'hA5); put(8'h05);                             // self-test
        idle(7000);
        if (uo_out[5:3] != 3'b110) begin errors++; $display("FAIL self-test status %b", uo_out); end
        else $display("info self-test passed");

        put(8'hA5); put(8'h04); idle(4);                    // CLEAR

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
        write_words(16'h0000, pw);
        lw = new[5];
        lw[0] = 32'd0;                                      // w5: no stuffing, NRZ
        lw[1] = 32'd1024; lw[2] = 32'd512; lw[3] = 32'd0;   // w6..w8: 64 cycles per bit
        lw[4] = (1 << 19) | (1 << 25);                      // w9: TX owns vpin 0, idles high
        write_words(16'h1005, lw);
        cw = new[2];
        cw[0] = 32'h101;                                    // lane 0 enabled, runtime SPI on
        cw[1] = 32'(7) << ($clog2(`LANES_TB * 4 + 1) + 2); // pad uo_out[0] <- lane 0 vpin 0: en, idle 1, idle_oe
        write_words(16'h1F00, cw);

        put(8'hA5); put(8'h03); put(crc[15:8]); put(crc[7:0]); idle(3);
        if (!uo_out[6]) begin errors++; $display("FAIL CRC check"); end

        put(8'hA5); put(8'h02); put(8'h00); put(8'h00); put(8'h04);   // read back 5 words
        for (int i = 0; i < 40; i++) begin put(8'h00); seen.push_back(uo_out); end
        lat = -1;
        for (int s = 0; s + 20 <= seen.size() && lat < 0; s++) begin
            bit ok;
            ok = 1;
            for (int j = 0; j < 20; j++) begin
                logic [31:0] x;
                x = pw[j / 4];
                if (seen[s + j] !== x[8*(3 - j % 4) +: 8]) ok = 0;
            end
            if (ok) lat = s;
        end
        if (lat < 0) begin errors++; $display("FAIL readback mismatch"); end
        else $display("info readback ok, first byte seen %0d clocks after the N byte", lat + 1);

        put(8'hA5); put(8'h06); idle(8);                    // COMMIT
        if (!uo_out[7]) begin errors++; $display("FAIL key not set"); end

        fork                                                // run mode: decode the UART byte
            begin
                logic [7:0] b;
                @(posedge rst_n);                              // ignore the mode-switch edge
                begin                                           // idle high from the switch, no glitch
                    int hi;
                    hi = 0;
                    while (uo_out[0] === 1'b1) begin @(posedge clk); hi++; end
                    idle_bad = hi < 40;                           // the first BITSTART is 64 cycles away
                    $display("info uo_out[0] high for %0d cycles before the start bit", hi);
                end
                repeat (32) @(posedge clk);
                for (int i = 0; i < 10; i++) begin
                    if (i >= 1 && i <= 8) b[i - 1] = uo_out[0];
                    repeat (64) @(posedge clk);
                end
                rx.push_back(b);
            end
            begin
                ui_in = 8'h01;                                  // SPI CS high, SCK low
                @(negedge clk) rst_n = 1'b1;
                repeat (1500) @(negedge clk);
            end
        join
        if (rx.size() != 1 || rx[0] !== 8'h55) begin errors++; $display("FAIL UART got %p", rx); end
        else $display("info lane 0 sent 0x55 on uo_out[0]");
        if (idle_bad) begin errors++; $display("FAIL uo_out[0] not idle high before the start bit"); end
        else $display("info uo_out[0] idle high from the mode switch to the start bit");

        spi_xfer(8'h00, 16'h3000, 32'd0, rd);
        if (rd !== 32'h11) begin errors++; $display("FAIL SPI status %h", rd); end
        spi_xfer(8'h00, 16'h2000, 32'd0, rd);
        if (rd[31:26] !== {2'd0, 4'd5}) begin errors++; $display("FAIL event %h", rd); end
        else $display("info event lane 0 code 5 at ts %0d", rd[25:0]);

        rst_n = 1'b0;                                       // load mode clears the key
        idle(8);
        if (uo_out[7] !== 1'b0) begin errors++; $display("FAIL key still set after entering load mode"); end
        else $display("info entering load mode cleared the run key");

        if (errors == 0) $display("PASS tb_tse_chip");
        else             $display("FAIL tb_tse_chip errors=%0d", errors);
        $finish;
    end
endmodule
