# SPDX-License-Identifier: Apache-2.0
"""The chip's measurements against the I2C judge's on the same trace.

The judge (verif.judges.i2c) reads SCL and SDA from the pins-only VCD and
checks each Standard-mode rule; here every interval it measures is recorded,
pass or fail, and paired with the chip's measurement of the same rule that
ends at the same instant. The chip's timestamps are placed on the VCD time
line by the first START, which both sides see.

    python3 examples/check/crosscheck.py OBLIGATIONS RECORDS VCD --from SECONDS
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
for p in (os.path.join(ROOT, "host"), ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

from tse_check import obfile, records, report  # noqa: E402
from verif.judges import common, i2c  # noqa: E402
from verif.judges.vcd import read_vcd  # noqa: E402

TOL = 0.1                       # ticks: the VCD keeps whole nanoseconds (0.06 ticks)


class _Recording(common.Result):
    def __init__(self, trace):
        super().__init__(trace)
        self.seen = []

    def check_min(self, t, rule, units, limit_s, text=""):
        self.seen.append((rule, t, self.trace.seconds(units)))
        super().check_min(t, rule, units, limit_s, text)


def judge(vcd, t_from, scl="uio_in2", sda="uio_in3"):
    """(judge Result, [(rule, end in seconds, interval in seconds)])."""
    trace = read_vcd(vcd, {"scl": scl, "sda": sda}, pullup=i2c.PULLUP)
    t0 = int(round(trace.units(t_from)))
    for s in trace.signals.values():
        s.crop(t0)
    keep, i2c.Result = i2c.Result, _Recording
    try:
        res = i2c.decode(trace, speed="sm")
    finally:
        i2c.Result = keep
    return res, [(r, trace.seconds(t), s) for r, t, s in res.seen]


def compare(results, events, start_code, jres, jseen, clock):
    """Pair chip and judge measurements. Returns (lines, ok)."""
    starts = [e.ts for e in events if e.code == start_code]
    if not starts or not jres.frames:
        return ["no START on one side"], False
    t_start = jres.trace.seconds(jres.frames[0].t0)
    to_s = lambda ts: t_start + (ts - starts[0]) / clock  # noqa: E731
    by_rule = {}
    for rule, t, s in jseen:
        by_rule.setdefault(rule, []).append((t, s))
    out, ok, worst, matched = [], True, 0.0, 0
    for r in results:
        theirs = by_rule.pop(r.ob.name, [])
        free = list(theirs)
        miss = 0
        for m in r.meas:
            end = to_s(m.t1)
            hit = [x for x in free if abs(x[0] - end) * clock < 1.0]
            if not hit:
                miss += 1
                continue
            free.remove(hit[0])
            d = m.ticks - hit[0][1] * clock
            worst = max(worst, abs(d))
            matched += 1
        good = miss == 0 and not free
        ok = ok and good
        out.append("%-18s chip %4d  judge %4d  %s" % (r.ob.name, len(r.meas), len(theirs),
                                                     "paired" if good else
                                                     "UNPAIRED: chip %d, judge %d" % (miss, len(free))))
    for rule, xs in sorted(by_rule.items()):
        out.append("%-18s chip    -  judge %4d  (no obligation)" % (rule, len(xs)))
    ok = ok and worst <= TOL
    out.append("%d intervals paired, largest difference %.3f ticks (%s)"
               % (matched, worst, "within %.2f" % TOL if worst <= TOL else "TOO LARGE"))
    return out, ok


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("obligations")
    ap.add_argument("records")
    ap.add_argument("vcd")
    ap.add_argument("--from", dest="t_from", type=float, default=0.0)
    a = ap.parse_args(argv)
    text = open(a.obligations).read()
    clock, obs = obfile.load(text)
    events = records.decode(records.parse_words(open(a.records).read()))[0]
    start = json.loads(text)["codes"]["start"]
    jres, jseen = judge(a.vcd, a.t_from)
    lines, ok = compare(report.check(events, obs), events, start, jres, jseen, clock)
    print("\n".join(lines))
    print("%s crosscheck" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
