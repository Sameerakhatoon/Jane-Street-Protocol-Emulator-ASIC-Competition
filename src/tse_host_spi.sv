`default_nettype none
// Runtime host interface: SPI target, mode 0, MSB first, on the RP2350's
// hardware SPI0 pins (ui_in[0] CS, ui_in[1] SCK, ui_in[2] MOSI, uo_out[3]
// MISO). The pins arrive already synchronized (from the crossbar's pad
// synchronizers, so each pin is synchronized exactly once) and are
// oversampled by clk. This block only turns bits into bytes: every 8 SCK
// rising edges deliver one byte to the command decoder (s_first marks the
// first byte after CS falls), and on the falling edge that ends a byte the
// decoder's current output byte is loaded for MISO. A read therefore sends one
// dummy byte after the address (the decoder fetches the word while it
// shifts) and then clocks out the data, as the byte bus does with strobes.
// The decoder updates its byte one or two cycles after s_valid, so half an
// SCK period must cover the synchronizer and that update: SCK up to about
// f_clk / 8.
module tse_host_spi (
    input  logic       clk,
    input  logic       rst_n,
    input  logic       cs_n,
    input  logic       sck,
    input  logic       mosi,
    output logic       miso,
    output logic       s_valid,
    output logic       s_first,
    output logic [7:0] s_data,
    input  logic [7:0] dout
);
    logic       sck_q, first;
    logic [2:0] bcnt;
    logic [6:0] osr;

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            sck_q <= 1'b0; first <= 1'b1; bcnt <= '0; osr <= '0; miso <= 1'b0;
            s_valid <= 1'b0; s_first <= 1'b0; s_data <= '0;
        end else begin
            sck_q   <= sck;
            s_valid <= 1'b0;
            if (cs_n) begin
                bcnt <= '0; first <= 1'b1;
            end else if (sck && !sck_q) begin
                s_data <= {s_data[6:0], mosi};
                bcnt   <= bcnt + 3'd1;
                if (bcnt == 3'd7) begin s_valid <= 1'b1; s_first <= first; first <= 1'b0; end
            end else if (!sck && sck_q) begin
                if (bcnt == 3'd0) begin miso <= dout[7]; osr <= dout[6:0]; end
                else              begin miso <= osr[6];  osr <= {osr[5:0], 1'b0}; end
            end
        end
endmodule
`default_nettype wire
