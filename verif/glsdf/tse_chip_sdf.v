// Gate-level bench top: the routed netlist of tt_um_tse_sk_chip under the tse_chip name the
// chip tests drive, with the run's SDF annotated (+sdf=<file>). Inputs reach the netlist the
// SDC input delay after the testbench sets them (base.sdc: 20 % of the 16.67 ns period), so
// the simulation sees the same arrival the timing signoff assumed. The tests set inputs right
// after a rising clock edge; without this delay a pin would change before the clock tree
// reaches the flops and race them.
`timescale 1ns/1ps
`default_nettype none
module tse_chip (
    input  wire [7:0] ui_in,
    output wire [7:0] uo_out,
    input  wire [7:0] uio_in,
    output wire [7:0] uio_out,
    output wire [7:0] uio_oe,
    input  wire       ena,
    input  wire       clk,
    input  wire       rst_n
);
    localparam real IN_DELAY = 3.334;
    wire [7:0] ui_d, uio_d;
    wire ena_d, rst_n_d;
    assign #(IN_DELAY) ui_d = ui_in;
    assign #(IN_DELAY) uio_d = uio_in;
    assign #(IN_DELAY) ena_d = ena;
    assign #(IN_DELAY) rst_n_d = rst_n;

    tt_um_tse_sk_chip u_dut (
        .ui_in(ui_d), .uo_out, .uio_in(uio_d), .uio_out, .uio_oe,
        .ena(ena_d), .clk, .rst_n(rst_n_d)
    );

    // +sdf= is the run's SDF without the SRAM macros, which Icarus cannot resolve by name;
    // +sdf_lo= and +sdf_hi= carry each macro's delays with the macro itself as scope.
    reg [8*512-1:0] sdf;
    initial begin
        if ($value$plusargs("sdf=%s", sdf)) $sdf_annotate(sdf, u_dut);
        else $display("tse_chip_sdf: no +sdf=, running without delays");
        if ($value$plusargs("sdf_lo=%s", sdf)) $sdf_annotate(sdf, u_dut.\u_chip.u_top.u_pmem.u_sram.u_macro_lo );
        if ($value$plusargs("sdf_hi=%s", sdf)) $sdf_annotate(sdf, u_dut.\u_chip.u_top.u_pmem.u_sram.u_macro_hi );
    end
endmodule
`default_nettype wire
