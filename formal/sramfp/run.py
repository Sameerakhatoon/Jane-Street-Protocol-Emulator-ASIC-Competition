#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""SRAM read data never steers the SRAM inputs in the same cycle.

The program SRAM is two 512x16 macros (u_macro_lo, u_macro_hi). Their
outputs A_DOUT reach the macro inputs (A_ADDR, A_DIN, A_MEN, A_WEN,
A_REN) through lane and arbiter logic in the netlist; the fixed fetch
slots make those paths unable to matter, which is what lets the layout
constraints exempt them from timing. The proof frees the read data of both
macros at once, so it also covers paths from one macro to the other; the
layout constraints only use the paths from a macro to its own inputs.
This runner proves it on the whole Tiny Tapeout top:

  1. flatten the chip (the source list of info.yaml) with each SRAM
     macro replaced by sram_free.sv, a stand-in whose read data is free;
  2. product.py builds fp_core: two copies sharing every register, each
     with its own free read data of both macros; it fails unless it finds
     the inputs of both macros, and prints the wires it bound;
  3. sby proves fp_props.sv: the inputs of each macro agree in both copies
     (task sdc: whenever that macro uses them; task strict: always).

Then each planted bug (a text substitution in a copy of the RTL) must make
the sdc task FAIL in BMC. UNKNOWN, ERROR, a timeout or a missing status
count as failures.

usage: run.py [--rtl DIR] [--build DIR] [--no-bugs] [--jobs N]
"""
import argparse
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TIMEOUT_S = 1800
BUG_DEPTH = 16
HARNESS = ["sram_free.sv", "fp_props.sv", "sramfp.sby", "product.py"]
PDK_STUB = ROOT / "test" / "rtl" / "sg13cmos5l_antennanp.v"     # the top's one PDK cell

# (name, file, old, new): each makes a slot read the word fetched one cycle
# earlier, so the fresh SRAM output steers the SRAM inputs in that cycle.
BUGS = [
    # the idle slot after lane 1's fetch slot also goes to lane 1
    ("spare_slot_to_last_lane", "tse_slot_arb.sv",
     "gnt[i]  = req[i] && cnt[1:0] == 2'(i);",
     "gnt[i]  = req[i] && (cnt[1:0] == 2'(i) || (i == LANES - 1 && cnt[1:0] == 2'(LANES)));"),
    # a lane's data slot moves to the cycle right after its fetch slot
    ("data_slot_after_fetch", "tse_slot_arb.sv",
     "dgnt[i] = dreq[i] && sys_slot && cnt[3:2] == 2'(i);",
     "dgnt[i] = dreq[i] && cnt[1:0] == 2'(i + 1) && cnt[3:2] == 2'(i);"),
]


def chip_sources(rtl):
    text = (ROOT / "info.yaml").read_text()
    body = text.split("source_files:")[1]
    files = []
    for ln in body.splitlines()[1:]:
        m = re.match(r'\s*-\s*"([^"]+)"', ln)
        if not m:
            break
        files.append(m.group(1))
    top = re.search(r'top_module:\s*"([^"]+)"', text).group(1)
    return [f for f in files if f != "tse_sram_bb.v"], top


def stage(rtl, out, bug=None):
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    files, top = chip_sources(rtl)
    for f in files:
        shutil.copy(rtl / f, out / f)
    for f in HARNESS:
        shutil.copy(HERE / f, out / f)
    shutil.copy(PDK_STUB, out / PDK_STUB.name)
    if bug:
        _, f, old, new = bug
        src = (out / f).read_text()
        if old not in src:
            return None
        (out / f).write_text(src.replace(old, new, 1))
    ys = (f"read_verilog -sv -formal {' '.join(files)} sram_free.sv {PDK_STUB.name}\n"
          f"prep -flatten -top {top}\n"
          "memory_map\nopt -keepdc -fast\nclk2fflogic\nopt_clean\n"
          "write_rtlil chip.il\n")
    (out / "flat.ys").write_text(ys)
    subprocess.run(["yosys", "-q", "-l", "flat.log", "flat.ys"], cwd=out, check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run([sys.executable, "product.py", "chip.il", "fp_core.il"], cwd=out, check=True)
    return out / "sramfp.sby"


def as_bmc(sby):
    text = sby.read_text().replace("mode prove", "mode bmc")
    sby.write_text(re.sub(r"depth \d+", f"depth {BUG_DEPTH}", text))


def run_sby(work, sby, jobs, tasks):
    t0 = time.time()
    try:
        subprocess.run(["sby", "-f", "-j", str(jobs), sby.name] + tasks, cwd=work,
                       timeout=TIMEOUT_S, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        pass
    return time.time() - t0


def status(d):
    f = d / "status"
    return f.read_text().split()[0] if f.exists() else "MISSING"


def task_time(d):
    log = d / "logfile.txt"
    if not log.exists():
        return "?"
    m = re.findall(r"Elapsed clock time \[H:MM:SS \(secs\)\]: [0-9:]+ \((\d+)\)", log.read_text())
    return f"{m[-1]} s" if m else "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtl", default=str(ROOT / "src"))
    ap.add_argument("--build", default=str(ROOT / "build" / "sramfp"))
    ap.add_argument("--no-bugs", action="store_true")
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args()
    rtl, build = Path(a.rtl).resolve(), Path(a.build).resolve()
    rows, bad = [], 0

    work = build / "proof"
    sby = stage(rtl, work)
    secs = run_sby(work, sby, a.jobs, ["sdc", "strict"])
    for t in ("sdc", "strict"):
        d = work / f"sramfp_{t}"
        st = status(d)
        bad += st != "PASS"
        rows.append(f"{'ok ' if st == 'PASS' else 'BAD'} proof {t:26s} got {st:8s} want PASS ({task_time(d)})")
    rows.append(f"    proof total {secs:.0f} s")

    for bug in ([] if a.no_bugs else BUGS):
        work = build / f"bug_{bug[0]}"
        sby = stage(rtl, work, bug)
        if sby is None:
            bad += 1
            rows.append(f"BAD bug {bug[0]}: stale, the RTL no longer contains the line")
            continue
        as_bmc(sby)
        run_sby(work, sby, 1, ["sdc"])
        d = work / "sramfp_sdc"
        st = status(d)
        bad += st != "FAIL"
        rows.append(f"{'ok ' if st == 'FAIL' else 'BAD'} bug {bug[0]:28s} got {st:8s} want FAIL ({task_time(d)})")

    print("\n".join(rows))
    print(f"{'FAILED' if bad else 'ALL OK'}: {bad} unexpected verdict(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
