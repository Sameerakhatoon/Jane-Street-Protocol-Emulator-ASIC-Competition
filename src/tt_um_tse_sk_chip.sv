`default_nettype none
// Tiny Tapeout top for the full chip: 2 lanes, latch configuration, 6x4 tiles. The SRAM macro sits at u_chip.u_top.u_pmem.u_sram.u_macro, the
// instance path config_chip.json places; keep these names.
module tt_um_tse_sk_chip (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
    tse_chip #(.LANES(2), .CFG_LATCH(1'b1)) u_chip (
        .ui_in, .uo_out, .uio_in, .uio_out, .uio_oe, .ena, .clk, .rst_n
    );
endmodule
`default_nettype wire
