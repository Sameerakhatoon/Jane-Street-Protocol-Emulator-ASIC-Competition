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
        ("idle_system_slot_to_lane", "ni2", "tse_slot_arb.sv",
         "gnt[i]  = req[i] && cnt[1:0] == 2'(i);",
         "gnt[i]  = req[i] && (cnt[1:0] == 2'(i) || (sys_slot && !sys_req && i == 1));"),
        ("data_slot_off_by_one", "own2", "tse_slot_arb.sv",
         "dgnt[i] = dreq[i] && sys_slot && cnt[3:2] == 2'(i);",
         "dgnt[i] = dreq[i] && cnt[1:0] == 2'd2 && cnt[3:2] == 2'(i);"),
    ]),
    "pmem": ("pmem", ["tse_slot_arb.sv", "tse_cfg_bank.sv", "tse_sram_512x32.sv", "tse_prog_mem.sv"], [
        ("fallback_ignores_slot", "own", "tse_prog_mem.sv",
         "f_gnt[i] = l_req[i] && l_fb[i] && cnt[1:0] == 2'(i);", "f_gnt[i] = l_req[i] && l_fb[i];"),
        ("fallback_slot_lent_by_lane0", "ni", "tse_prog_mem.sv",
         "f_gnt[i] = l_req[i] && l_fb[i] && cnt[1:0] == 2'(i);",
         "f_gnt[i] = l_req[i] && l_fb[i] && (cnt[1:0] == 2'(i) || (i == 1 && !s_req[0]));"),
        ("fallback_word_from_host_address", "own", "tse_prog_mem.sv",
         "if (f_gnt != '0) fb_ra <= fa;", "if (f_gnt != '0) fb_ra <= h_addr[2:0];"),
    ]),
    "rule": ("rule", ["tse_resp_rule.sv"], [
        ("tick_delay_one_late", "timing", "tse_resp_rule.sv",
         "if (delayed && cedge) tk <= cfg_tdly;", "if (delayed && cedge) tk <= cfg_tdly + 8'd1;"),
        ("offset_one_short", "timing", "tse_resp_rule.sv",
         "assign wait0 = {1'b0, wctl[7:4]} + {4'd0, delayed};", "assign wait0 = {1'b0, wctl[7:4]};"),
        ("chain_ignored", "chain", "tse_resp_rule.sv",
         "&& (k == 0 || !cfg_ctl[14*k+13] || hit_flags[(k == 0) ? 0 : k - 1])", ""),
        ("position_ignored", "ref", "tse_resp_rule.sv",
         "&& (cfg_pos[9*k+8] || cfg_pos[9*k +: 8] == cnt);", ";"),
        ("hold_ignored", "hold", "tse_resp_rule.sv",
         "if (left == 4'd0) drv_en <= cfg_hold;", "if (left == 4'd0) drv_en <= 1'b0;"),
        ("hold_never_released", "hold", "tse_resp_rule.sv",
         "if (cfg_hold && seq_wr && !(any && wctl[10])) drv_en <= 1'b0;", ""),
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
    into a trace, which sby reports as ERROR instead of FAIL. BUGRUN drops the
    induction helpers and their register probes (the connect lines after an
    "# induction helpers" line), so the named property alone must catch the
    bug (a mutant may also optimise a probed register away)."""
    lines, helpers = [], False
    for ln in sby.read_text().splitlines():
        helpers = (helpers or ln.strip() == "# induction helpers") and ln.strip() != ""
        if ln.strip().endswith("mode prove"):
            pre = ln[:ln.index("mode")]
            lines += [pre + "mode bmc", pre + f"depth {depth}"]
        elif ln.strip() == "[script]":
            lines += [ln, "verilog_defines -DBUGRUN"]
        elif not (helpers and ln.startswith("connect -set ")):
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
