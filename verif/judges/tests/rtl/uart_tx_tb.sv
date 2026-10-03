`timescale 1ns/1ps
`default_nettype none
// Drives the chip's UART transmitter at 60 MHz and 115200 baud and dumps
// only the TX pin, so the judge sees nothing but what a logic analyser would.
module uart_tx_tb;
    logic       clk = 1'b0;
    logic       rst_n = 1'b0;
    logic       start = 1'b0;
    logic [7:0] data = 8'h00;
    wire        tick16, busy, txd;
    string      path;
    logic [7:0] msg [0:4];

    always #8.3333 clk = ~clk;

    // 16 * 115200 / 60e6 * 2^24, rounded
    tse_nco #(.W(24)) nco (.clk(clk), .rst_n(rst_n), .en(1'b1), .inc(24'd515396), .tick(tick16));
    tse_uart_tx tx (.clk(clk), .rst_n(rst_n), .tick16(tick16), .start(start), .data(data),
                    .busy(busy), .txd(txd));

    initial begin
        msg[0] = 8'h41; msg[1] = 8'h00; msg[2] = 8'hFF; msg[3] = 8'h55; msg[4] = 8'h0A;
        if (!$value$plusargs("vcd=%s", path)) path = "uart_tx.vcd";
        $dumpfile(path);
        $dumpvars(0, txd);
        repeat (8) @(posedge clk);
        rst_n = 1'b1;
        for (int i = 0; i < 5; i++) begin
            @(posedge clk);
            while (busy) @(posedge clk);
            data = msg[i];
            start = 1'b1;
            @(posedge clk);
            start = 1'b0;
        end
        @(posedge clk);
        while (busy) @(posedge clk);
        #20000;
        $finish;
    end
endmodule
`default_nettype wire
