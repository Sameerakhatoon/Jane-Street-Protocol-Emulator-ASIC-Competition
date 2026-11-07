# SPDX-License-Identifier: Apache-2.0
"""Command line: decode one protocol from a VCD and print PASS or FAIL.

    python3 -m verif.judges uart trace.vcd --map rx=uo_out0 --baud 115200
    python3 -m verif.judges i2c trace.vcd --map scl=uio_in2 --map sda=uio_in3 --speed fm
    python3 -m verif.judges can trace.vcd --map rx=can --bitrate 500000 --expect want.txt

Every decoded frame is printed with its start time, then every violation
with the measured value and the limit. With --expect FILE the frame keys
must equal the lines of FILE (blank lines and lines starting with # are
ignored). The last line is "PASS ..." or "FAIL ...". Exit status: 0 PASS,
1 FAIL, 2 bad usage or unreadable trace. A trace with no frames fails
unless --allow-empty is given.
"""
import argparse
import difflib
import sys

from . import can, eth10, i2c, jtag, manchester, ps2, spi, swd, uart, usb
from .common import fmt_time
from .vcd import VcdError, read_vcd

PROTOCOLS = {"uart": uart, "spi": spi, "i2c": i2c, "can": can, "swd": swd, "ps2": ps2,
             "jtag": jtag, "usb": usb, "manchester": manchester, "eth10": eth10}


def parser():
    p = argparse.ArgumentParser(prog="python3 -m verif.judges",
                                description="Independent protocol judges for pins-only VCD traces.")
    sub = p.add_subparsers(dest="protocol", required=True)
    for name, mod in PROTOCOLS.items():
        sp = sub.add_parser(name, help=(mod.__doc__ or "").splitlines()[0])
        sp.add_argument("trace")
        sp.add_argument("--map", action="append", default=[], metavar="ROLE=SIGNAL",
                        help="roles: %s (required: %s)" % (", ".join(mod.ROLES), ", ".join(mod.REQUIRED) or "any one"))
        sp.add_argument("--expect", metavar="FILE", help="expected frame keys, one per line")
        sp.add_argument("--allow-empty", action="store_true")
        sp.add_argument("--quiet", action="store_true", help="print only violations and the verdict")
        sp.add_argument("--from", dest="t_from", type=float, metavar="SECONDS",
                        help="judge only from this time on (for example after a load phase)")
        mod.add_args(sp)
    return p


def read_expect(path):
    with open(path) as f:
        return [ln.strip() for ln in f if ln.strip() and not ln.lstrip().startswith("#")]


def run(argv, out=sys.stdout):
    args = parser().parse_args(argv)
    mod = PROTOCOLS[args.protocol]
    mapping = {}
    for m in args.map:
        role, sep, sig = m.partition("=")
        if not sep or role not in mod.ROLES:
            print("error: bad --map %r; roles are %s" % (m, ", ".join(mod.ROLES)), file=sys.stderr)
            return 2
        mapping[role] = sig
    missing = [r for r in mod.REQUIRED if r not in mapping]
    if missing or not mapping:
        print("error: --map needed for %s" % ", ".join(missing or mod.ROLES), file=sys.stderr)
        return 2
    try:
        trace = read_vcd(args.trace, mapping, pullup=mod.PULLUP)
    except (OSError, VcdError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 2
    if args.t_from is not None:
        t0 = int(round(trace.units(args.t_from)))
        for s in trace.signals.values():
            s.crop(t0)
    res = mod.decode(trace, **{k: getattr(args, k) for k in mod.OPTS})

    if not args.quiet:
        for f in res.frames:
            print("%14s  %s%s" % (fmt_time(trace.seconds(f.t0)), f.key, "  (%s)" % f.info if f.info else ""), file=out)
    for v in res.violations:
        print("VIOLATION %14s  %s: measured %s, limit %s%s" % (
            fmt_time(trace.seconds(v.t)), v.rule, v.measured, v.limit,
            " (%s)" % v.text if v.text else ""), file=out)

    problems = []
    if res.violations:
        problems.append("%d violations" % len(res.violations))
    if not res.frames and not args.allow_empty:
        problems.append("no frames decoded")
    if args.expect:
        want = read_expect(args.expect)
        got = res.keys()
        if want != got:
            problems.append("frames differ from %s" % args.expect)
            diff = list(difflib.unified_diff(want, got, "expected", "decoded", lineterm="", n=1))
            for ln in diff[:40]:
                print("  " + ln, file=out)
    verdict = "FAIL" if problems else "PASS"
    detail = "; ".join(problems) if problems else "%d violations" % len(res.violations)
    print("%s %s: %d frames, %s" % (verdict, args.protocol, len(res.frames), detail), file=out)
    return 1 if problems else 0


def main():
    sys.exit(run(sys.argv[1:]))
