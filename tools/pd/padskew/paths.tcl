# SPDX-License-Identifier: Apache-2.0
# tools/pd/padskew/paths.tcl: OpenSTA on one corner of a finished run (LIBS, NETLIST, SDC,
# SPEF from padskew.sh). Prints the worst rise and fall path of every lane pin for max and
# min delay: outputs from the clock to the port, inputs from the port to the capturing flop.
# padskew.py turns these into tile-internal pin delays.
foreach lib $::env(LIBS) { read_liberty $lib }
read_verilog $::env(NETLIST)
link_design tt_um_tse_sk_chip
read_sdc $::env(SDC)
read_spef $::env(SPEF)
# Pin delays measured from the tile boundary: no external input or output budget.
set data_in [get_ports {ui_in[*] uio_in[*]}]
set data_out [get_ports {uo_out[*] uio_out[*] uio_oe[*]}]
set_input_delay 0 -clock clk $data_in
set_output_delay 0 -clock clk $data_out

foreach p $data_out {
    foreach pd {max min} {
        foreach rf {rise fall} {
            puts "PADPATH out [get_full_name $p] $pd $rf"
            report_checks -${rf}_to $p -path_delay $pd -format full_clock_expanded -digits 4
        }
    }
}
foreach p $data_in {
    foreach pd {max min} {
        foreach rf {rise fall} {
            puts "PADPATH in [get_full_name $p] $pd $rf"
            report_checks -${rf}_from $p -path_delay $pd -format full_clock_expanded -digits 4
        }
    }
}
puts "PADPATH end"
