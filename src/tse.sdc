# SPDX-License-Identifier: Apache-2.0
# src/tse.sdc: LibreLane's default constraints plus the TSE rules.
read_sdc $::env(SCRIPTS_DIR)/base.sdc

# Setup keeps the PDK margin. Hold compares launch and capture on the same clock
# edge, so jitter cancels; derating and propagated clocks cover the rest.
set_clock_uncertainty -setup $::env(CLOCK_UNCERTAINTY_CONSTRAINT) [get_clocks $::env(CLOCK_PORT)]
set_clock_uncertainty -hold 0.10 [get_clocks $::env(CLOCK_PORT)]

# Half-cycle paths exist (inputs are sampled on both clock edges). The clock
# reaches the tile through the same pad and mux as data, so its duty cycle is
# not exactly 50 %: give rise-to-fall and fall-to-rise paths 1 ns of margin.
set_clock_uncertainty 1.0 -rise_from [get_clocks $::env(CLOCK_PORT)] -fall_to [get_clocks $::env(CLOCK_PORT)]
set_clock_uncertainty 1.0 -fall_from [get_clocks $::env(CLOCK_PORT)] -rise_to [get_clocks $::env(CLOCK_PORT)]

# The SRAM output never steers the functional SRAM inputs in the same cycle:
# the fixed slots grant a lane only in its own slot, and a lane uses a fetched
# word only in the cycle after that slot. The test port stays timed.
foreach tse_sram {u_chip.u_top.u_pmem.u_sram.u_macro_lo u_chip.u_top.u_pmem.u_sram.u_macro_hi} {
    if {[llength [get_cells $tse_sram]] != 1} { error "tse.sdc: SRAM instance $tse_sram not found" }
    set_false_path -through [get_pins $tse_sram/A_DOUT*]         -to [get_pins [list $tse_sram/A_ADDR* $tse_sram/A_DIN* $tse_sram/A_MEN $tse_sram/A_WEN $tse_sram/A_REN]]
}

# Tiny Tapeout pad ring as the tile sees it (tt-multiplexer, ihp-sg13cmos5l):
# clk, rst_n, ui_in and uio_in leave the mux through a zeroing buffer, one
# sg13cmos5l_and2_2, and ena through one sg13cmos5l_buf_2. Every output
# (uo_out, uio_out, uio_oe) drives one sg13cmos5l_mux4_1 data input (2.8 fF)
# plus a sg13cmos5l_antennanp diode (1.1 fF) over a short wire; the mux's own
# STA puts 10 fF on these tile connections, which also covers the wire.
set_driving_cell -lib_cell sg13cmos5l_and2_2 -pin X [all_inputs]
set_driving_cell -lib_cell sg13cmos5l_buf_2 -pin X [get_ports ena]
set_load 0.010 [all_outputs]
