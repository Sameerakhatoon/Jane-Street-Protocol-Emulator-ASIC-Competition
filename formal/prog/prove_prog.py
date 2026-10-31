#!/usr/bin/env python3
"""Per-program proofs: the compiler's generated harness on the real lane RTL.

For one spec this script runs the compiler (tools.tsec build --emit proof),
binds every generated harness to tse_lane through prog_lane.sv and runs
SymbiYosys. Per role with a window obligation:

  prove    the generated checker as emitted (window and environment from
           the spec): must PASS
  exact    the same checker with the window narrowed to the latency the
           compiler's static analysis reports: must PASS, so the compiler's
           cycle numbers are the hardware's
  late     planted bug: the program's first delay before the sample is
           stretched to the maximum; must FAIL
  fast     planted bug: the environment may start the next frame after
           FAST_QUIET quiet cycles instead of the spec's minimum; must FAIL

Every FAIL is translated back into the spec's terms (which window, which
trigger, which environment assumption, how many ticks late) from the
counterexample trace. UNKNOWN, ERROR, a timeout or a missing status are
failures.

usage: prove_prog.py SPEC [--rtl DIR] [--build DIR] [-D NAME=VALUE] [--no-bugs]
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
TIMEOUT_S = 600
FAST_QUIET = 300
LANE_RTL = ["tse_isa_pkg.sv", "tse_regs_pkg.sv", "tse_crc.sv", "tse_stuffer.sv", "tse_codec.sv",
            "tse_dpll.sv", "tse_resp_rule.sv", "tse_nco.sv", "tse_timer.sv", "tse_seq.sv",
            "tse_lane.sv"]
CHECK_RE = re.compile(r"tse_window_check #\(\.LO\((\d+)\), \.HI\((\d+)\), \.QUIET\((\d+)\), "
                      r"\.W\((\d+)\)\) ob(\d+) \(\s*\n\s*\.clk, \.rst\(init\), "
                      r"\.trig_in\(pin_in\[(\d+)\]\), \.trig_val\(1'b([01])\),\s*\n\s*"
                      r"\.resp_out\(([^)]+)\)")
NOTE_RE = re.compile(r"// line (\d+): (.+?) within \[([\d.]+), ([\d.]+)\] ticks after (\w+)\((\w+)\)")
STATIC_RE = re.compile(r"line\s+(\d+) .*?#0: sample in \[(\d+), (\d+)\) after edge")


class Check:
    """One generated window checker and what the spec says about it."""
    def __init__(self, m, note):
        self.lo, self.hi, self.quiet, self.w, self.k = (int(m.group(i)) for i in range(1, 6))
        self.trig_pin, self.trig_val, self.resp = int(m.group(6)), int(m.group(7)), m.group(8)
        self.line, self.what = int(note.group(1)), note.group(2)
        self.a, self.b = float(note.group(3)), float(note.group(4))
        self.edge, self.wire = note.group(5), note.group(6)
        self.static = None          # (first, last + 1) issue distance D from the report

    def text(self):
        return (f"line {self.line}: {self.what} within [{self.a}, {self.b}] ticks after "
                f"{self.edge}({self.wire})")


def sh(cmd, cwd, timeout=None):
    try:
        return subprocess.run(cmd, cwd=cwd, timeout=timeout, capture_output=True, text=True)
    except subprocess.TimeoutExpired:
        return None


def lane_words(rtl):
    pkg = (rtl / "tse_regs_pkg.sv").read_text()
    return {m.group(1): int(m.group(2))
            for m in re.finditer(r"localparam logic \[4:0\] LW_(\w+) = 5'd(\d+);", pkg)}


def cfg_value(regs, words):
    v = 0
    for name, val in regs.items():
        if name not in words:
            raise SystemExit(f"prove_prog: no bank word for register {name}")
        v |= (int(val, 16) & 0xFFFFFFFF) << (32 * words[name])
    return v


def lir_roles(text):
    """{role: [(pc, line text)]} from the compiler's .lir listing."""
    roles, cur = {}, None
    for ln in text.splitlines():
        m = re.match(r"; lane (\d+): role (\w+)", ln)
        if m:
            cur = roles.setdefault(m.group(2), [])
        elif cur is not None and re.match(r"\s*\d+\s", ln):
            cur.append((int(ln.split()[0]), ln))
    return roles


def report_static(text):
    """{role: {line: (first D, last D + 1)}} from the report's window rows."""
    out, cur = {}, None
    for ln in text.splitlines():
        m = re.match(r"== role (\w+)", ln)
        if m:
            cur = out.setdefault(m.group(1), {})
        m = STATIC_RE.search(ln)
        if m and cur is not None:
            cur[int(m.group(1))] = (int(m.group(2)), int(m.group(3)))
    return out


def stretch_first_delay(hexw, sample_pc):
    """Planted bug: the first DLY before the sampling instruction gets d = 511."""
    for pc in range(sample_pc):
        w = hexw[pc]
        if w >> 9 == 0x70:                  # DLY: op 7, side-set 0, sub-op 0
            out = list(hexw)
            out[pc] = 0xE1FF
            return out, pc, w & 0x1FF
    return None, None, None


def sby_text(tasks, rtl_files, cfg, mask, hexname):
    lines = ["[tasks]"] + [t for t in tasks] + ["", "[options]", "mode prove", "vcd_sim on", "",
             "[engines]", "aiger suprove", "", "[script]",
             "read_verilog -formal -sv -defer " + " ".join(rtl_files + ["prog_lane.sv",
                                                                         "tse_window_check.sv"])]
    lines += [f"{t}: read_verilog -formal -sv -defer top_{t}.sv" for t in tasks]
    lines += [f"chparam -set CFG 1024'h{cfg:x} -set SAMPLE_PCS 256'h{mask:x} prog_lane",
              "hierarchy -top top", "prep -top top", "flatten",
              "techmap t:$shiftx t:$shift", "opt_expr", "setundef -anyseq", "",
              "[files]"] + rtl_files + ["prog_lane.sv", "tse_window_check.sv"]
    lines += [f"top_{t}.sv" for t in tasks] + sorted({hexname, "prog.hex"})
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- traces

def read_vcd(path):
    """(steps, {full name: [value per step]}) for a SymbiYosys trace."""
    ids, scope, changes, times = {}, [], [], []
    t = None
    for ln in path.read_text().splitlines():
        tok = ln.split()
        if not tok:
            continue
        if tok[0] == "$scope":
            scope.append(tok[2])
        elif tok[0] == "$upscope":
            scope.pop()
        elif tok[0] == "$var":
            ids.setdefault(tok[3], []).append(".".join(scope + [tok[4]]))
        elif tok[0].startswith("#"):
            t = int(tok[0][1:])
            times.append(t)
        elif t is not None and tok[0][0] in "b":
            changes.append((t, tok[1], int(tok[0][1:].replace("x", "0") or "0", 2)))
        elif t is not None and tok[0][0] in "01xz" and len(tok) == 1:
            changes.append((t, tok[0][1:], 1 if tok[0][0] == "1" else 0))
    stamps = sorted(set(times))
    index = {s: i for i, s in enumerate(stamps)}
    vals = {}
    for s, ident, v in changes:
        for name in ids.get(ident, []):
            vals.setdefault(name, {})[index[s]] = v
    out = {}
    for name, ch in vals.items():
        row, cur = [], 0
        for i in range(len(stamps)):
            cur = ch.get(i, cur)
            row.append(cur)
        out[name] = row
    clk = out.get("top.clk")
    if clk and len(set(clk)) > 1:          # one step per rising clock edge
        keep = [i for i in range(len(clk)) if clk[i] and (i == 0 or not clk[i - 1])]
        out = {k: [v[i] for i in keep] for k, v in out.items()}
        return len(keep), out
    return len(stamps), out


def find(sig, suffix):
    for name, row in sig.items():
        if name.endswith(suffix):
            return row
    return None


def translate(trace, chk):
    """Spec-language reading of a failing window trace."""
    n, sig = read_vcd(trace)
    pre = f"ob{chk.k}."
    armed, age = find(sig, pre + "armed"), find(sig, pre + "age")
    quiet, pin = find(sig, pre + "quiet"), find(sig, ".pin_in")
    if armed is None or age is None or pin is None:
        return [f"  counterexample: {trace} ({n} steps; checker signals not in the trace)"]
    bit = lambda s: (pin[s] >> chk.trig_pin) & 1
    trig = next((s for s in range(n - 1, 0, -1) if armed[s] and not armed[s - 1]), None)
    if trig is None:
        return [f"  counterexample: {trace} ({n} steps; no trigger found)"]
    t0 = trig - 1                                       # the cycle the trigger shows
    prev = max((s for s in range(1, t0) if bit(s) != bit(s - 1)), default=None)
    out = [f"  REJECT {chk.text()}"]
    q = quiet[t0] if quiet else None
    gap = f"{t0 - prev} cycles after the previous change of {chk.wire}" if prev is not None \
        else f"with {chk.wire} stable since reset"
    out.append(f"  trigger: {chk.edge}({chk.wire}) seen by the lane in cycle {t0}, {gap}; "
               f"the environment assumption allows it after {chk.quiet} quiet cycles"
               + (f" (stable count {q})" if q is not None else ""))
    last = n - 1
    resp = next((s for s in range(trig, n) if armed[s] and s + 1 < n and not armed[s + 1]), None)
    d = (age[resp] - 1) if resp is not None else (age[last] - 1)
    if resp is not None and age[resp] < chk.lo:
        out.append(f"  response: sampled {d} cycles after the trigger, at [{d}, {d + 1}) ticks "
                   f"after the edge: early by {chk.a - d:.2f} ticks")
    else:
        late = d + 1 - chk.b
        word = "" if resp is not None else "at least "
        out.append(f"  response: {'sampled' if resp is not None else 'no sample by cycle ' + str(last) + ', so'} "
                   f"{word}{d} cycles after the trigger, at {word}[{d}, {d + 1}) ticks after the "
                   f"edge: late by {word}{late:.2f} ticks")
    out.append(f"  trace: {trace}")
    return out


# ---------------------------------------------------------------- runs

def run_sby(work, task):
    t0 = time.time()
    sh(["sby", "-f", "prog.sby", task], work, TIMEOUT_S)
    st = work / f"prog_{task}" / "status"
    return (st.read_text().split()[0] if st.exists() else "MISSING"), time.time() - t0


def stage(work, rtl, rtl_files, gen, tops, hexw, tasks, cfg, mask):
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    for f in rtl_files:
        shutil.copy(rtl / f, work / f)
    shutil.copy(HERE / "prog_lane.sv", work)
    shutil.copy(gen / "tse_window_check.sv", work)
    for t, text in tops.items():
        (work / f"top_{t}.sv").write_text(text)
    hexname = re.search(r'PROG_HEX\("([^"]+)"\)', next(iter(tops.values()))).group(1)
    for name in {hexname, "prog.hex"}:
        (work / name).write_text("".join(f"{w:04x}\n" for w in (hexw + [0] * 256)[:256]))
    (work / "prog.sby").write_text(sby_text(tasks, rtl_files, cfg, mask, hexname))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--rtl", default=str(ROOT / "src"))
    ap.add_argument("--build", default=str(ROOT / "build" / "formal" / "prog"))
    ap.add_argument("-D", action="append", default=[])
    ap.add_argument("--no-bugs", action="store_true")
    a = ap.parse_args()
    rtl, spec = Path(a.rtl).resolve(), Path(a.spec).resolve()
    base = Path(a.build).resolve() / spec.stem
    gen = base / "tsec"
    if base.exists():
        shutil.rmtree(base)
    cmd = [sys.executable, "-m", "tools.tsec", "build", str(spec), "--emit", "proof,lir,json,report",
           "-o", str(gen)] + [x for d in a.D for x in ("-D", d)]
    r = sh(cmd, ROOT)
    if r is None or r.returncode:
        print(r.stdout + r.stderr if r else "tsec timed out")
        print("FAILED: the compiler rejected the spec, no harness to run")
        return 1
    stem = spec.stem
    lir = lir_roles((gen / f"{stem}.lir").read_text())
    static = report_static((gen / f"{stem}.report.txt").read_text())
    lanes = {ln["role"]: ln for ln in json.loads((gen / f"{stem}.json").read_text())["lanes"]}
    words = lane_words(rtl)
    rows, bad = [], 0
    for top in sorted((gen / "proof").glob("top_*.sv")):
        role = top.stem[4:]
        text = top.read_text().replace("tse_lane #(", "prog_lane #(")
        notes = list(NOTE_RE.finditer(text))
        checks = [Check(m, nt) for m, nt in zip(CHECK_RE.finditer(text), notes)]
        if not checks:
            rows.append(f"--  {role:10s} no window obligation in the generated harness: nothing to prove")
            continue
        wins = [pc for pc, ln in lir[role] if "OB-WIN" in ln]
        if len(wins) != len(checks) or len(notes) != len(checks):
            raise SystemExit(f"prove_prog: {role}: {len(checks)} checkers, {len(wins)} OB-WIN rows")
        samples = [pc for pc, c in zip(wins, checks) if c.resp == "sample_strobe"]
        if len(samples) > 1:
            raise SystemExit(f"prove_prog: {role}: one sample_strobe serves only one sample obligation")
        mask = sum(1 << pc for pc in samples)
        cfg = cfg_value(lanes[role]["regs"], words)
        hexw = [int(w, 16) for w in (gen / "proof" / f"{role}.hex").read_text().split()]
        exact = text
        for c in checks:
            c.static = static.get(role, {}).get(c.line)
            if c.static:
                old = f".LO({c.lo}), .HI({c.hi})"
                exact = exact.replace(old, f".LO({c.static[0] + 1}), .HI({c.static[1]})", 1)
        work = base / role
        stage(work, rtl, LANE_RTL, gen / "proof", {"prove": text, "exact": exact}, hexw,
              ["prove", "exact"], cfg, mask)
        for t, want in (("prove", "PASS"), ("exact", "PASS")):
            st, secs = run_sby(work, t)
            ok = st == want
            bad += not ok
            extra = ""
            if t == "exact":
                extra = " ".join(f"line {c.line} D in [{c.static[0]}, {c.static[1] - 1}]"
                                 for c in checks if c.static)
            rows.append(f"{'ok ' if ok else 'BAD'} {role:10s} {t:6s} got {st:7s} want {want} "
                        f"({secs:.0f} s) {extra}")
            if st == "FAIL":
                for c in checks:
                    rows += translate(work / f"prog_{t}" / "engine_0" / "trace.vcd", c)
        if a.no_bugs:
            continue
        c0 = checks[0]
        late, pc, d = stretch_first_delay(hexw, samples[0]) if samples else (None, None, None)
        bugs = []
        if late:
            bugs.append(("late", f"DLY {d} at pc {pc} becomes DLY 511", text, late))
        fast = text.replace(f".QUIET({c0.quiet})", f".QUIET({FAST_QUIET})", 1)
        bugs.append(("fast", f"next frame allowed after {FAST_QUIET} quiet cycles, not {c0.quiet}",
                     fast, hexw))
        for name, what, ttext, words_ in bugs:
            bw = base / f"{role}_bug_{name}"
            stage(bw, rtl, LANE_RTL, gen / "proof", {"prove": ttext}, words_, ["prove"], cfg, mask)
            st, secs = run_sby(bw, "prove")
            ok = st == "FAIL"
            bad += not ok
            rows.append(f"{'ok ' if ok else 'BAD'} {role:10s} bug {name:5s} got {st:7s} want FAIL "
                        f"({secs:.0f} s): {what}")
            if st == "FAIL":
                if name == "fast":
                    c0.quiet = FAST_QUIET
                rows += translate(bw / "prog_prove" / "engine_0" / "trace.vcd", c0)
    print("\n".join(rows))
    print(f"{'FAILED' if bad else 'ALL OK'}: {bad} unexpected verdict(s)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
