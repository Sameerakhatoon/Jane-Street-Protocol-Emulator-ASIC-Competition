`default_nettype none
// Full-chip wrapper with the Tiny Tapeout ports; tt_um_tse_sk_chip
// instantiates it as u_chip. rst_n low = load mode: ui_in is the loader
// byte, uio_in[0] its strobe and uio_in[1] the frame bit, uo_out the
// readback byte; uio pins are inputs. rst_n high = run mode: all 16 pads
// belong to the crossbar, except uo_out[3] (MISO) when the runtime SPI is
// enabled. The pin multiplexer only switches when rst_n changes.
module tse_chip #(
    parameter int LANES      = 3,
    parameter bit CFG_LATCH  = 1'b1,
    parameter int EVQ_DEPTH  = 4,
    parameter int CHIP_BANKS = 2
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
    logic [15:0] pad_out, pad_oe;
    logic [7:0]  ld_dout;
    logic        load_mode, spi_en, spi_miso;

    tse_top #(.LANES(LANES), .CFG_LATCH(CFG_LATCH), .EVQ_DEPTH(EVQ_DEPTH), .CHIP_BANKS(CHIP_BANKS)) u_top (
        .clk, .rst_n, .pad_in({uio_in, ui_in}), .pad_out, .pad_oe,
        .load_mode, .ld_dout, .spi_en, .spi_miso
    );

    assign uo_out  = load_mode ? ld_dout
                   : {pad_out[7:4], spi_en ? spi_miso : pad_out[3], pad_out[2:0]};
    assign uio_out = pad_out[15:8];
    assign uio_oe  = load_mode ? 8'h00 : pad_oe[15:8];
    wire _unused = &{ena, pad_oe[7:0], 1'b0};
endmodule
`default_nettype wire
