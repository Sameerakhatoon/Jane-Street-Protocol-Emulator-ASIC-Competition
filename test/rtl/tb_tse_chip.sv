`timescale 1ns/1ps
// Chip-level bring-up test through the real TT pins, as the RP2350 drives them.
// Load mode: loaded clear on entry, BEGIN, BIST, program, lane and chip
// configuration with WR/WRB, COMMIT, STAT, RDB readback, LOAD_CRC.
// Run mode over the SPI target: RUN, a UART byte from lane 0 on uo_out[0]
// (idle high from the mode switch), STATUS, ID, the event log (TIME then
// lane 0 code 5), IRQ read-to-clear, a refused write setting host error.
// Back in load mode loaded reads 0.
module tb_tse_chip;
    import tse_regs_pkg::*;

`include "chip_h.svh"

    localparam logic [31:0] LANE_CFG_UART = (1 << LR_LANE_CFG_TX_OWNS_PIN_LO) | (1 << LR_LANE_CFG_IDLE_LO);
    logic [7:0]  rx [$];
    bit          idle_bad;

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

        p = new[12];
        p[0] = SET(7, 8'hD0);                               // DPLL on, line idle high
        p[1] = SET(2, 9);  p[2] = SET(3, 0);
        p[3] = XFR(2, 0, 8'h21);                            // TXCTL = 9: send 10 bits
        p[4] = SET(2, 8'hAA); p[5] = SET(5, 8'h02);         // X = 0x02AA = {stop, 0x55, start}
        p[6] = XFR(2, 0, 8'h20);                            // TXDATA = {Y, X}
        p[7] = WAITI(1, 1, 6, 0, 0, 0);                     // wait TX idle
        p[8] = tse_isa_pkg::EMIT_MATCH | (16'd5 << tse_isa_pkg::EMIT_CODE_LO);   // EMIT 5
        p[9] = SET(6, 5);                                   // interrupt bits 0 and 2
        p[10] = JMP(0, 10); p[11] = JMP(0, 11);
        pw = new[6];
        foreach (pw[i]) pw[i] = {p[2*i + 1], p[2*i]};
        ld_wrb(16'h0000, pw);
        // lane 0: no stuffing, NRZ; 64 cycles per bit; TX owns vpin 0, idles high
        ld_wr(LANE_HOST_BASE + 16'(LR_STUFF_CFG), 32'd0);
        ld_wr(LANE_HOST_BASE + 16'(LR_CODEC_CFG), 32'd0);
        ld_wr(LANE_HOST_BASE + 16'(LR_DPLL_PERIOD), 32'd1024);
        ld_wr(LANE_HOST_BASE + 16'(LR_DPLL_SPT), 32'd512);
        ld_wr(LANE_HOST_BASE + 16'(LR_DPLL_SJW), 32'd0);
        ld_wr(LANE_HOST_BASE + 16'(LR_LANE_CFG), LANE_CFG_UART);
        ld_wr(LANE_HOST_BASE + 16'(LR_LANE_PROG), 32'd0);   // window 0, start 0
        ld_wr(HR_PAD_OUT, 32'h1C0);                         // uo_out[0] <- lane 0 vpin 0: en, idle 1, idle_oe
        ld_wr(HR_EV_CTL, 32'h3);                            // log lane 0

        ld(8'h12, 1);                                       // COMMIT
        for (int k = 3; k >= 0; k--) ld(8'(~crc >> (8 * k)), 0);
        ld_stat(s);
        check("STAT after COMMIT", 32'(s), 32'h07);

        ld_rdb(16'h0000, pw.size(), rb);
        foreach (pw[i]) if (rb[i] !== pw[i]) begin errors++; $display("FAIL readback word %0d %h %h", i, rb[i], pw[i]); end
        ld_rdb(HR_LOAD_CRC, 1, rb);
        check("LOAD_CRC", rb[0], ~crc);
        ld_rdb(LANE_HOST_BASE + 16'(LR_LANE_CFG), 1, rb);
        check("lane 0 LANE_CFG readback", rb[0], LANE_CFG_UART);
        ld_rdb(HR_PAD_OUT, 1, rb);
        check("PAD_OUT[0] reads 0 (chip configuration is write-only)", rb[0], 32'h0);

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
        check("ID", rd, {12'd0, 4'd4, 1'(`LATCH_TB), 3'd2, 4'd1, 8'h54});
        spi_rd(HR_EV_POP, rd);
        check("first record is TIME, epoch 0", rd, 32'h3F00_0000);
        spi_rd(HR_EV_POP, rd);
        check("lane 0 event code 5, X[7:0] 0xAA", {rd[31:16], 16'd0}, 32'h05AA_0000);
        spi_rd(HR_IRQ, rd);
        check("IRQ", rd & 32'hF, 32'h5);
        spi_rd(HR_IRQ, rd);
        check("IRQ after the clearing read", rd, 32'h0);
        spi_wr(LANE_HOST_BASE + 16'(LR_LANE_CFG), 32'd0);                // running lane: refused
        spi_rd(HR_STATUS, rd);
        check("STATUS after a refused write", rd, 32'h10F);
        spi_rd(LANE_HOST_BASE + 16'(LR_LANE_CFG), rd);
        check("refused write left the word", rd, LANE_CFG_UART);

        rst_n = 1'b0;                                       // load mode clears loaded
        repeat (8) @(negedge clk);
        ld_stat(s);
        check("STAT after re-entering load mode, loaded bit", 32'(s[0]), 32'd0);

        if (errors == 0) $display("PASS tb_tse_chip");
        else             $display("FAIL tb_tse_chip errors=%0d", errors);
        $finish;
    end
endmodule
