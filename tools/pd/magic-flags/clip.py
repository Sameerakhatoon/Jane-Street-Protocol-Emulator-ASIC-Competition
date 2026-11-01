# tools/pd/magic-flags/clip.py: one flattened GDS per Magic flag, a window of MARGIN um around
# the flag. Run by magic-flags.sh inside the LibreLane image (klayout -b -r).
import os
import re

import pya

MARGIN = 15.0  # wider than the 10 um run length of the wide-metal rules

gds, rpt, out = os.environ["GDS"], os.environ["RPT"], os.environ["OUT"]
flags = []
rule = None
for line in open(rpt):
    f = line.split()
    if len(f) == 4 and all(re.match(r"^-?[\d.]+um$", x) for x in f):
        flags.append((rule, [float(x[:-2]) for x in f]))
    elif line.strip() and not line.startswith(("-", "[")):
        rule = line.strip()

ly = pya.Layout()
ly.read(gds)
top = ly.top_cell()
with open(os.path.join(out, "flags.txt"), "w") as fl:
    for i, (rule, (x0, y0, x1, y1)) in enumerate(flags):
        box = pya.DBox(x0 - MARGIN, y0 - MARGIN, x1 + MARGIN, y1 + MARGIN).to_itype(ly.dbu)
        clip = pya.Layout()
        clip.dbu = ly.dbu
        cell = clip.create_cell("clip")  # drc.tcl loads this name
        cell.copy_tree(ly.cell(ly.clip(top.cell_index(), box)))
        cell.flatten(True)
        clip.write(os.path.join(out, "clips", f"f{i:03d}.gds"))
        fl.write(f"f{i:03d}|{rule}|{x0} {y0} {x1} {y1}\n")
print(f"{len(flags)} windows")
