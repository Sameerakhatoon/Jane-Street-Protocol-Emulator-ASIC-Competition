#!/usr/bin/env python3
"""Run every proof, then every planted-bug variant, and enforce the verdicts.

Each job copies its harness and the RTL files it needs from RTL_DIR into
build/formal/<job>/ and runs its .sby file: every task must PASS. Each
planted bug copies the job again, applies one text substitution to an RTL
copy (the RTL itself is never touched) and runs one task, which must FAIL:
a proof only counts once it catches a broken design (bug runs are BMC). A substitution that no
longer matches the RTL is reported as stale, so a bug cannot silently stop
being planted. UNKNOWN, ERROR, a timeout or a missing status count as
failures.

usage: run_formal.py [--rtl DIR] [--only job,job] [--no-bugs]
"""
import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
TIMEOUT_S = 900

# job: (harness dir, RTL files, planted bugs as (name, task, file, old, new))
JOBS = {
    "crc": ("crc", ["tse_crc.sv"], [
        ("tx_load_no_xorout", "full", "tse_crc.sv",
         "else if (tx_load)           crc <= t;", "else if (tx_load)           crc <= crc;"),
        ("refin_ignored", "equiv", "tse_crc.sv",
         "assign din      = busy ? (cfg_refin ? bsr[0] : bsr[7]) : bit_in;",
         "assign din      = busy ? bsr[7] : bit_in;"),
    ]),
    "stuffer": ("stuffer", ["tse_stuffer.sv"], [
        ("stuff_bit_not_counted", None, "tse_stuffer.sv",
         "trun  <= (tx_dout == tlast) ? inc_sat(trun) : 4'd1;",
         "trun  <= tx_pending ? 4'd0 : (tx_dout == tlast) ? inc_sat(trun) : 4'd1;"),
    ]),
    "codec": ("codec", ["tse_codec.sv"], [
        ("manchester_wrong_half", None, "tse_codec.sv",
         "MAN:     begin rx_bit <= rx_s ^ cfg_inv;", "MAN:     begin rx_bit <= h1 ^ cfg_inv;"),
    ]),
    "arb": ("arb", ["tse_slot_arb.sv"], [
        ("idle_system_slot_to_lane", "ni3", "tse_slot_arb.sv",
         "gnt[i]  = req[i] && cnt[1:0] == 2'(i);",
         "gnt[i]  = req[i] && (cnt[1:0] == 2'(i) || (sys_slot && !sys_req && i == 1));"),
        ("data_slot_off_by_one", "own3", "tse_slot_arb.sv",
         "dgnt[i] = dreq[i] && sys_slot && cnt[3:2] == 2'(i);",
         "dgnt[i] = dreq[i] && cnt[1:0] == 2'd2 && cnt[3:2] == 2'(i);"),
    ]),
    "dpll": ("dpll", ["tse_dpll.sv"], [
        ("sample_strobe_repeats", None, "tse_dpll.sv",
         "s_done <= enable && !bit_start && (s_done || sample);", "s_done <= 1'b0;"),
    ]),
}


def tasks_of(sby):
    text = sby.read_text()
    if "[tasks]" not in text:
        return [""]
    body = text.split("[tasks]")[1].split("\n[")[0]
    return [ln.split()[0] for ln in body.splitlines() if ln.strip()]


def status(d):
    f = d / "status"
    return f.read_text().split()[0] if f.exists() else "MISSING"


def run_job(work, sby, task):
    t0 = time.time()
    cmd = ["sby", "-j", "2", "-f", sby.name] + ([task] if task else [])
    try:
        subprocess.run(cmd, cwd=work, timeout=TIMEOUT_S, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        pass
    return time.time() - t0


def as_bmc(sby, depth=12):
    """Bug runs use BMC: a counterexample from abc pdr cannot always be replayed
    into a trace, which sby reports as ERROR instead of FAIL."""
    lines = []
    for ln in sby.read_text().splitlines():
        if ln.strip().endswith("mode prove"):
            pre = ln[:ln.index("mode")]
            lines += [pre + "mode bmc", pre + f"depth {depth}"]
        else:
            lines.append(ln.replace("abc pdr", "smtbmc yices"))
    sby.write_text("\n".join(lines) + "\n")


def stage(rtl, out, hdir, files):
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HERE / hdir, out)
    for f in files:
        shutil.copy(rtl / f, out / f)
    return next(out.glob("*.sby"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rtl", default=str(ROOT / "src"))
    ap.add_argument("--build", default=str(ROOT / "build" / "formal"))
    ap.add_argument("--only", default="")
    ap.add_argument("--no-bugs", action="store_true")
    a = ap.parse_args()
    rtl, build = Path(a.rtl).resolve(), Path(a.build).resolve()
    only = [j for j in a.only.split(",") if j]
    rows, bad = [], 0
    for job, (hdir, files, bugs) in JOBS.items():
        if only and job not in only:
            continue
        sby = stage(rtl, build / job, hdir, files)
        secs = run_job(build / job, sby, None)
        for t in tasks_of(sby):
            st = status(build / job / (sby.stem + (f"_{t}" if t else "")))
            ok = st == "PASS"
            bad += not ok
            rows.append(f"{'ok ' if ok else 'BAD'} {job:8s} {t or '-':14s} got {st:8s} want PASS")
        rows.append(f"    {job:8s} ({secs:.0f} s)")
        for name, task, f, old, new in ([] if a.no_bugs else bugs):
            out = build / f"{job}_bug_{name}"
            sby = stage(rtl, out, hdir, files)
            src = (out / f).read_text()
            if old not in src:
                bad += 1
                rows.append(f"BAD {job:8s} bug {name}: stale, the RTL no longer contains the line")
                continue
            (out / f).write_text(src.replace(old, new, 1))
            as_bmc(sby)
            secs = run_job(out, sby, task)
            st = status(out / (sby.stem + (f"_{task}" if task else "")))
            ok = st == "FAIL"
            bad += not ok
            rows.append(f"{'ok ' if ok else 'BAD'} {job:8s} bug {name:24s} got {st:8s} want FAIL"
                        f" ({secs:.0f} s)")
    print("\n".join(rows))
    print(f"{'FAILED' if bad else 'ALL OK'}: {bad} unexpected verdict(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
