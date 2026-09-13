`default_nettype none
// Runtime host interface: SPI target, mode 0, MSB first, on the RP2350's
// hardware SPI0 pins (ui_in[0] CS, ui_in[1] SCK, ui_in[2] MOSI, uo_out[3]
// MISO). The pins arrive already synchronized (from the crossbar's pad
// synchronizers, so each pin is synchronized exactly once). Oversampling
// limits SCK to about f_clk / 12 (5 MHz at 60 MHz): MISO changes about
// five cycles after the SCK falling edge at the pin, and must be valid half a
// period later.
// Frame: cmd[7:0] (bit 7 = write), addr[15:0], data[31:0]. A read fetches
// the register after the address and returns it in the data phase.
module tse_host_spi (
    input  logic        clk,
    input  logic        rst_n,
    input  logic        cs_n,
    input  logic        sck,
    input  logic        mosi,
    output logic        miso,
    output logic        h_we,
    output logic        h_re,
    output logic [15:0] h_addr,
    output logic [31:0] h_wdata,
    input  logic [31:0] h_rdata
);
    logic       sck_q, rise, fall, wr, rd_pend;
    logic [5:0] cnt;
    logic [31:0] sr;
    logic [31:0] obuf;
    logic [15:0] addr_q;

    assign rise    = sck && !sck_q;
    assign fall    = !sck && sck_q;
    assign h_addr  = addr_q;
    assign h_wdata = sr[31:0];

    always_ff @(posedge clk or negedge rst_n)
        if (!rst_n) begin
            sck_q <= 1'b0; cnt <= '0; sr <= '0; obuf <= '0; miso <= 1'b0; addr_q <= '0;
            wr <= 1'b0; rd_pend <= 1'b0; h_we <= 1'b0; h_re <= 1'b0;
        end else begin
            sck_q <= sck;
            h_we  <= 1'b0;
            h_re  <= 1'b0;
            if (rd_pend) begin obuf <= h_rdata; rd_pend <= 1'b0; end
            if (cs_n) begin
                cnt <= '0;
            end else if (rise) begin
                sr  <= {sr[30:0], mosi};
                cnt <= cnt + 6'd1;
                if (cnt == 6'd7)  wr <= sr[6];                 // bit 7 of cmd just completed
                if (cnt == 6'd23) addr_q <= {sr[14:0], mosi};
                if (cnt == 6'd23 && !wr) begin h_re <= 1'b1; rd_pend <= 1'b1; end
                if (cnt == 6'd55 && wr) h_we <= 1'b1;
            end else if (fall && cnt >= 6'd24) begin
                miso <= obuf[31];
                obuf <= {obuf[30:0], 1'b0};
            end
        end
endmodule
`default_nettype wire
