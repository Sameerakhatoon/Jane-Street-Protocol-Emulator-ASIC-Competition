`default_nettype none
// UART test Tiny Tapeout top: a UART transmitter, plus a receiver in echo mode.
//   ui_in[7:0]  byte to send
//   uio_in[0]   a rising edge sends ui_in once
//   uio_in[1]   1 = echo mode: ui_in[3] is UART RX and every received byte
//               is sent back on TX; strobes are ignored
//   uo_out[0]   UART TX, idles high
//   uo_out[1]   TX busy, from the strobe until the stop bit ends
//   uo_out[2]   toggles per received byte
//   uo_out[3]   RX framing error, held until the next good byte
// 115200 baud from 60 MHz; at 50 MHz use BAUD16_INC = 618475.
// The module name is set here and in info.yaml (top_module) only; the test
// Makefile and tb.v take it from info.yaml.
module tt_um_tse_sk #(
    parameter logic [23:0] BAUD16_INC = 24'd515396     // 16 * 115200 * 2^24 / 60e6
) (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
    logic       rst_s_n, ld_rst_n, sys_rst_n, load_mode;
    logic [7:0] ui_s;
    logic [1:0] uio_s;
    logic       stb_q, tick16, busy, txd;
    logic       rx_valid, rx_ferr, rx_tog, rx_err;
    logic [7:0] rx_data, hold;
    logic       req;

    tse_rst_sync u_rst (
        .clk, .rst_n, .eng_rst_n(rst_s_n), .ld_rst_n, .sys_rst_n, .load_mode
    );

    // Every pin is synchronized exactly once; RX reads the synchronized ui_in[3].
    tse_sync #(.W(8)) u_sync_ui  (.clk, .rst_n(rst_s_n), .d(ui_in),       .q(ui_s));
    tse_sync #(.W(2)) u_sync_uio (.clk, .rst_n(rst_s_n), .d(uio_in[1:0]), .q(uio_s));

    tse_nco #(.W(24)) u_baud (.clk, .rst_n(rst_s_n), .en(1'b1), .inc(BAUD16_INC), .tick(tick16));

    tse_uart_rx u_rx (
        .clk, .rst_n(rst_s_n), .tick16, .rxd(ui_s[3] | !uio_s[1]),
        .valid(rx_valid), .ferr(rx_ferr), .data(rx_data)
    );

    // One-byte holding register: an echo that arrives while the previous
    // frame is still in its stop bit waits here instead of being dropped.
    always_ff @(posedge clk or negedge rst_s_n)
        if (!rst_s_n) begin
            stb_q <= 1'b0; req <= 1'b0; hold <= '0; rx_tog <= 1'b0; rx_err <= 1'b0;
        end else begin
            stb_q <= uio_s[0];
            if (uio_s[1] ? rx_valid : uio_s[0] && !stb_q) begin
                req  <= 1'b1;
                hold <= uio_s[1] ? rx_data : ui_s;
            end else if (!busy) begin
                req <= 1'b0;
            end
            if (rx_valid) rx_tog <= !rx_tog;
            if (rx_valid || rx_ferr) rx_err <= rx_ferr;
        end

    tse_uart_tx u_tx (
        .clk, .rst_n(rst_s_n), .tick16, .start(req), .data(hold), .busy, .txd
    );

    assign uo_out  = {4'b0, rx_err, rx_tog, busy, txd};
    assign uio_out = 8'b0;
    assign uio_oe  = 8'b0;
    // The UART test design has no load mode, so only the engine reset is used.
    wire _unused = &{ena, uio_in[7:2], ld_rst_n, sys_rst_n, load_mode, 1'b0};
endmodule
`default_nettype wire
