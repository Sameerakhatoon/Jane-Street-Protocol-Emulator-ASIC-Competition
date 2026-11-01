"""verif/glsdf/sdf_for_icarus.py <in.sdf> <out dir>: the run's SDF in a form Icarus can annotate.

Writes <out dir>/icarus.sdf and one <out dir>/sram_<macro>.sdf per SRAM macro.
- Icarus aborts (NULL handle in vpi_scan) on an INTERCONNECT that ends on a bit of a vector port
  of the SRAM model (u_macro_lo.A_ADDR[3] and so on), so those wire delays are dropped; the
  largest dropped delay is printed, so the loss is on record.
- It cannot resolve the escaped macro instance names (u_chip\\.u_top\\...u_macro_lo) or per-bit
  IOPATHs (A_DOUT[0]), and a failed CELL ends the whole annotation. So the macro CELLs leave
  icarus.sdf, and each macro gets its own SDF with an empty INSTANCE, annotated with the macro
  as scope (tse_chip_sdf.v), holding one A_CLK to A_DOUT path: per field the largest over the
  bits, which matches the model's (posedge A_CLK *> A_DOUT) path.
"""
import os
import re
import sys

src, out = sys.argv[1:3]
text = open(src).read()
head_end = text.index(" (CELL")
head, body = text[:head_end], text[head_end:]
# VOLTAGE and TEMPERATURE give no typical value (1.200::1.200), which Icarus reports as an error
head = "".join(l for l in head.splitlines(True) if not l.lstrip().startswith(("(VOLTAGE", "(TEMPERATURE")))
cells = re.split(r"\n(?= \(CELL\b)", body.rstrip().rstrip(")").rstrip())

kept, dropped, worst, macros = [], 0, 0.0, {}
for cell in cells:
    if 'CELLTYPE "RM_IHPSG13' in cell:
        name = re.search(r"\(INSTANCE (\S+)\)", cell).group(1).replace("\\", "").rsplit(".", 1)[-1]
        rise, fall = [0.0] * 3, [0.0] * 3
        for r, f in re.findall(r"\(IOPATH A_CLK A_DOUT\[\d+\] \(([\d.:]+)\) \(([\d.:]+)\)\)", cell):
            rise = [max(a, float(b)) for a, b in zip(rise, r.split(":"))]
            fall = [max(a, float(b)) for a, b in zip(fall, f.split(":"))]
        macros[name] = (rise, fall)
        continue
    lines = []
    for line in cell.split("\n"):
        if "(INTERCONNECT" in line and "u_macro_" in line:
            dropped += 1
            for v in re.findall(r"[\d.]+(?=[:)])", line.split(None, 3)[3]):
                worst = max(worst, float(v))
            continue
        lines.append(line)
    kept.append("\n".join(lines))

open(os.path.join(out, "icarus.sdf"), "w").write(head + "\n".join(kept) + "\n)\n")
fmt = lambda v: ":".join(f"{x:.3f}" for x in v)
for name, (rise, fall) in macros.items():
    open(os.path.join(out, f"sram_{name}.sdf"), "w").write(
        head + f' (CELL\n  (CELLTYPE "RM_IHPSG13_1P_512x16_c2_bm_bist")\n  (INSTANCE)\n  (DELAY\n   (ABSOLUTE\n'
        f"    (IOPATH (posedge A_CLK) A_DOUT ({fmt(rise)}) ({fmt(fall)}))\n   )\n  )\n )\n)\n")
print(f"sdf_for_icarus: dropped {dropped} SRAM pin wires (largest {worst:.3f} ns); "
      + ", ".join(f"{n} A_CLK to A_DOUT {fmt(r)} ns" for n, (r, _f) in sorted(macros.items())))
