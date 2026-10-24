"""Run a manifest of judge cases and print one summary table.

    python3 -m verif.judges.run_judges demos.txt
    python3 verif/judges/run_judges.py demos.txt --verbose

The manifest has one case per line (blank lines and # comments ignored):

    NAME  PROTOCOL  VCD  EXPECT  [OPTIONS...]

VCD and EXPECT are relative to the manifest's folder; EXPECT is a file of
frame keys or "-" for none. OPTIONS are the protocol's command line
options (--map ROLE=SIGNAL, --baud, ...), split like a shell does. A case
whose VCD does not exist is a SKIP, and a skip counts as a failure unless
the case line carries --allow-skip: a demo that produced no trace must not
look like a pass. Exit status 0 only when every case passes or is an
allowed skip; 2 for an unreadable manifest.

Example lines, from demos/stand_in.txt (the OpenOCD and TAP ping demos on
the stand-in top, recorded by demos/record.sh; make judges-demos judges-run):

    openocd_jtag jtag  jtag.vcd jtag.expect --map tck=ui_in2 --map tms=ui_in3 --map tdi=ui_in4 --map tdo=uo_out3
    openocd_swd  swd   swd.vcd  swd.expect  --map swclk=ui_in5 --map swdio=swdio --map host_oe=swdio_host --map target_oe=swdio_chip
    tap_ping_tx  eth10 eth.vcd  -           --map td=uo_out1
"""
import argparse
import contextlib
import io
import os
import shlex
import sys

if __package__ in (None, ""):
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    from verif.judges import cli
else:
    from . import cli


class Case:
    def __init__(self, line_no, name, protocol, vcd, expect, opts, allow_skip):
        self.line_no = line_no
        self.name = name
        self.protocol = protocol
        self.vcd = vcd
        self.expect = expect
        self.opts = opts
        self.allow_skip = allow_skip
        self.result = ""
        self.frames = "-"
        self.violations = "-"
        self.output = []


class ManifestError(Exception):
    pass


def read_manifest(path):
    base = os.path.dirname(os.path.abspath(path))
    cases = []
    with open(path) as f:
        for n, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            try:
                words = shlex.split(line)
            except ValueError as e:
                raise ManifestError("%s:%d: %s" % (path, n, e))
            if len(words) < 4:
                raise ManifestError("%s:%d: need NAME PROTOCOL VCD EXPECT" % (path, n))
            name, proto, vcd, expect = words[:4]
            if proto not in cli.PROTOCOLS:
                raise ManifestError("%s:%d: unknown protocol %r" % (path, n, proto))
            opts = words[4:]
            allow = "--allow-skip" in opts
            opts = [o for o in opts if o != "--allow-skip"]
            vcd = os.path.join(base, vcd)
            expect = None if expect == "-" else os.path.join(base, expect)
            cases.append(Case(n, name, proto, vcd, expect, opts, allow))
    if not cases:
        raise ManifestError("%s: no cases" % path)
    return cases


def run_case(c):
    if not os.path.exists(c.vcd):
        c.result = "SKIP"
        c.output = ["trace not found: %s" % c.vcd]
        return c.allow_skip
    argv = [c.protocol, c.vcd] + c.opts
    if c.expect:
        argv += ["--expect", c.expect]
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            rc = cli.run(argv, out)
    except SystemExit as e:
        rc = 2 if e.code else 0
    c.output = out.getvalue().splitlines() + err.getvalue().splitlines()
    lines = out.getvalue().splitlines()
    c.violations = str(sum(1 for ln in lines if ln.startswith("VIOLATION")))
    last = lines[-1].split() if lines else []
    if len(last) >= 3 and last[0] in ("PASS", "FAIL"):
        c.frames = last[2]
    c.result = {0: "PASS", 1: "FAIL"}.get(rc, "ERROR")
    return rc == 0


def main(argv=None, out=sys.stdout):
    p = argparse.ArgumentParser(prog="run_judges", description="Run judge cases from a manifest.")
    p.add_argument("manifest")
    p.add_argument("--verbose", action="store_true", help="print every case's output, not only failures")
    args = p.parse_args(argv)
    try:
        cases = read_manifest(args.manifest)
    except (OSError, ManifestError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    ok = [run_case(c) for c in cases]
    w = max(4, max(len(c.name) for c in cases))
    print("%-*s  %-10s  %6s  %10s  %s" % (w, "case", "protocol", "frames", "violations", "result"), file=out)
    for c, good in zip(cases, ok):
        res = c.result
        if res == "SKIP":
            res += " (allowed)" if c.allow_skip else " (counts as failure)"
        print("%-*s  %-10s  %6s  %10s  %s" % (w, c.name, c.protocol, c.frames, c.violations, res), file=out)
    for c, good in zip(cases, ok):
        if args.verbose or not good:
            print("", file=out)
            print("== %s (line %d)" % (c.name, c.line_no), file=out)
            for ln in c.output:
                print("   " + ln, file=out)
    n_fail = ok.count(False)
    n_skip = sum(1 for c in cases if c.result == "SKIP")
    print("", file=out)
    print("%s: %d cases, %d passed, %d failed, %d skipped" % (
        "FAIL" if n_fail else "PASS", len(cases), sum(1 for c in cases if c.result == "PASS"), n_fail, n_skip),
        file=out)
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
