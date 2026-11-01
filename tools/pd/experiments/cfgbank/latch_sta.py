#!/usr/bin/env python3
"""Write OpenSTA scripts that time the latch bank of a finished run three ways.

usage: latch_sta.py <run dir> <out dir>    (writes <out dir>/<corner>-<mode>.tcl)
The flow's own STA leaves every latch unclocked: the latch enables come from
flops (en_q, clr_q), not from the clock tree, so the latch D pins are
unconstrained endpoints and nothing launched by a latch is timed.
  A  as the flow sees it (counts unconstrained endpoints)
  B  each enable flop output is a generated clock, high for one cycle in two
     (rises and falls on clk rising edges), which is the write protocol
  C  every latch forced transparent: D to Q becomes a plain combinational arc,
     so flop -> latch -> consumer flop must fit in one cycle (pessimistic)
"""
import glob
import os
import re
import sys

run, out = sys.argv[1], sys.argv[2]
final = os.path.join(run, "final")
nl = glob.glob(f"{final}/nl/*.nl.v")[0]
top = os.path.basename(nl).split(".")[0]
cells = re.findall(r"^\s*(sg13cmos5l_\w+)\s+(\S+)\s*\((.*?)\);", open(nl).read(), re.M | re.S)
latches, enables = [], []
for typ, name, body in cells:
    pins = dict(re.findall(r"\.(\w+)\(\s*(.*?)\s*\)", body))
    if re.match(r"sg13cmos5l_dl[hl]", typ):
        latches.append(name)
    # Escaped Verilog name: a leading backslash, matched here as any one character.
    if re.fullmatch(r".bank\.(en_q\[\d+\]|clr_q)", pins.get("Q", "")):
        enables.append(name)
lib = os.environ["PDK_ROOT"] + "/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/lib/sg13cmos5l_stdcell_{}.lib"
corners = {"typ": "typ_1p20V_25C", "slow": "slow_1p08V_125C", "fast": "fast_1p32V_m40C"}
cells_l = "[get_cells {" + " ".join(latches) + "}]"
os.makedirs(out, exist_ok=True)
for short, c in corners.items():
    head = f"""read_liberty {lib.format(c)}
read_verilog {nl}
link_design {top}
read_spef {glob.glob(f"{final}/spef/nom/*.spef")[0]}
read_sdc {glob.glob(f"{final}/sdc/*.sdc")[0]}
set lq [get_pins -of_objects {cells_l} -filter "direction==output"]
set ld [get_pins -of_objects {cells_l} -filter "name==D"]
"""
    tail = """check_setup -unconstrained_endpoints
report_checks -path_delay max -to $ld -format full -digits 3
report_checks -path_delay min -to $ld -format full -digits 3
report_checks -path_delay max -through $lq -format full -digits 3
report_checks -path_delay min -through $lq -format full -digits 3
puts "setup [sta::format_time [sta::worst_slack_cmd max] 3] hold [sta::format_time [sta::worst_slack_cmd min] 3]"
"""
    modes = {
        "A": "",
        "B": "".join(f"create_generated_clock -name g{i} -source [get_ports clk] -edges {{1 3 5}} [get_pins {e}/Q]\n"
                     for i, e in enumerate(enables)) + "set_propagated_clock [all_clocks]\n",
        "C": f"set_case_analysis 0 [get_pins -of_objects {cells_l} -filter \"name==GATE_N\"]\n",
    }
    for mode, body in modes.items():
        with open(f"{out}/{short}-{mode}.tcl", "w") as f:
            f.write(head + body + tail)
print(f"{len(latches)} latches, {len(enables)} enable flops -> {out}")
