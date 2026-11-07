# SPDX-License-Identifier: Apache-2.0
"""Command line of the Check-mode analysis.

    tse_check report OBLIGATIONS RECORDS [--clock HZ]
    tse_check sweep OBLIGATIONS --cmd "CAPTURE {offset}" --from A --to B [--step S]
                    [--all] [--clock HZ]

RECORDS holds popped EV_POP words, one hex word per line ('-' reads
standard input). For a sweep, CAPTURE is a shell command run once per offset
with {offset} replaced; it prints the record words of that capture. Exit
status: 0 all pass, 1 a violation or a failing step, 2 a usage or input error.
"""
import argparse
import subprocess
import sys

from . import obfile, records, report, sweep


def _read(path):
    return sys.stdin.read() if path == "-" else open(path).read()


def _capture(cmd):
    def run(offset):
        p = subprocess.run(cmd.replace("{offset}", str(offset)), shell=True,
                           capture_output=True, text=True)
        if p.returncode != 0:
            raise records.RecordError("capture at offset %d failed: %s" % (offset, p.stderr.strip()))
        return records.parse_words(p.stdout)
    return run


def main(argv=None):
    ap = argparse.ArgumentParser(prog="tse_check", description="Check-mode timing analysis")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("report", help="timing compliance of one capture")
    r.add_argument("obligations")
    r.add_argument("records")
    s = sub.add_parser("sweep", help="margin sweep over stepped offsets")
    s.add_argument("obligations")
    s.add_argument("--cmd", required=True)
    s.add_argument("--from", dest="lo", type=int, required=True)
    s.add_argument("--to", dest="hi", type=int, required=True)
    s.add_argument("--step", type=int, default=1)
    s.add_argument("--all", action="store_true", help="do not stop at the first failing step")
    for p in (r, s):
        p.add_argument("--clock", type=float, help="chip clock in Hz (overrides the file)")
    a = ap.parse_args(argv)
    try:
        clock, obs = obfile.load(_read(a.obligations), a.clock)
        if a.cmd == "report":
            events, _ = records.decode(records.parse_words(_read(a.records)))
            results = report.check(events, obs)
            text, bad = report.lines(results, clock), report.overall(results) != report.PASS
        else:
            if a.step == 0:
                raise ValueError("step must not be 0")
            offs = range(a.lo, a.hi + (1 if a.step > 0 else -1), a.step)
            sw = sweep.run(_capture(a.cmd), obs, offs, stop=not a.all)
            text, bad = sweep.lines(sw, clock), sw.first_fail is not None
    except (ValueError, OSError) as e:
        sys.stderr.write("tse_check: %s\n" % e)
        return 2
    sys.stdout.write("\n".join(text) + "\n")
    return 1 if bad else 0
