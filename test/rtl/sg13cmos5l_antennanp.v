// SPDX-License-Identifier: Apache-2.0
// Stand-in for the one PDK cell the RTL instantiates: the antenna diode on ena in
// tt_um_tse_sk_chip (outside SIM). Lint and formal flows read it instead of the
// cell library, so they elaborate the real top. A diode has no function.
`default_nettype none
/* verilator lint_off UNUSEDSIGNAL */
module sg13cmos5l_antennanp (
    input wire A
);
endmodule
/* verilator lint_on UNUSEDSIGNAL */
`default_nettype wire
