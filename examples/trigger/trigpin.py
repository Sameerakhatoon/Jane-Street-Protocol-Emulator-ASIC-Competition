"""Judge the trigger-out pin from the pins-only VCD.

Ground truth comes from the trace, not from the chip: the I2C judge decodes
the transactions from SCL and SDA, and a small matcher picks the ones the
trigger must catch (a write to 0x50 whose first byte is 0x10, then at most
one more byte and a STOP). Each must have exactly one pulse on uo_out[7]
starting a fixed number of cycles after its STOP, and there must be no
other pulse. That number comes from the latency table: an input that
changes in cycle k is seen by a WAIT in k + 2, the monitor program EMITs 2
cycles after its WAIT ends, and the pad shows the trigger 3 cycles after
the EMIT, so from cycle k + 7 on. The harness applies an input it stamps at
cycle n in the first half of cycle n - 1 and stamps an output at the first
cycle that shows it, so the trace shows one cycle less.

    python3 examples/trigger/trigpin.py VCD --from SECONDS --width CYCLES
"""
import argparse
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from tools.tseasm import isa  # noqa: E402
from verif.judges import i2c  # noqa: E402
from verif.judges.vcd import read_vcd  # noqa: E402

FCLK = 60e6
PROGRAM = 2                     # cycles from the end of the WAIT to the EMIT (i2c_cond.s)
HARNESS = 1                     # the trace stamps an input one cycle after it changes
MATCH = re.compile(r"^S 50W A 10 A ([0-9A-F]{2} [AN] )?P$")


def pulses(sig, t0):
    """[(rise, fall)] of a signal from t0 on (file units)."""
    out, rise = [], None
    for t, v in zip(sig.times, sig.levels):
        if t < t0:
            continue
        if v == 1 and rise is None:
            rise = t
        elif v == 0 and rise is not None:
            out.append((rise, t))
            rise = None
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("vcd")
    ap.add_argument("--from", dest="t_from", type=float, default=0.0)
    ap.add_argument("--width", type=int, required=True)
    a = ap.parse_args(argv)
    lat = isa.LATENCY["port_to_wait"] + PROGRAM + isa.LATENCY["emit_to_trig_port"] - HARNESS

    tr = read_vcd(a.vcd, {"scl": "uio_in2", "sda": "uio_in3", "trig": "uo_out7"}, pullup=True)
    t0 = int(round(tr.units(a.t_from)))
    for s in (tr["scl"], tr["sda"]):
        s.crop(t0)
    res = i2c.decode(tr, speed="sm")
    cyc = lambda units: tr.seconds(units) * FCLK  # noqa: E731
    want = [f for f in res.frames if MATCH.match(f.key)]
    got = pulses(tr["trig"], t0)
    print("transactions %d, matching %d, trigger pulses %d, expected delay %d cycles (%.1f ns)"
          % (len(res.frames), len(want), len(got), lat, lat / FCLK * 1e9))
    bad = []
    for f in res.frames:
        mine = [p for p in got if 0 <= cyc(p[0] - f.t1) < 2 * lat]
        hit = MATCH.match(f.key) is not None
        if mine:
            d, w = cyc(mine[0][0] - f.t1), cyc(mine[0][1] - mine[0][0])
            print("  %-30s pulse %.2f cycles after the STOP, %.2f cycles wide" % (f.key, d, w))
            if not hit:
                bad.append("pulse after a near miss: %s" % f.key)
            elif abs(d - lat) > 0.1 or abs(w - a.width) > 0.1:
                bad.append("%s: delay %.2f, width %.2f" % (f.key, d, w))
        else:
            print("  %-30s no pulse" % f.key)
            if hit:
                bad.append("no pulse for %s" % f.key)
    if len(got) != len(want):
        bad.append("%d pulses for %d matching transactions" % (len(got), len(want)))
    if not want:
        bad.append("no matching transaction in the trace")
    print("%s trigger pin%s" % ("FAIL" if bad else "PASS", ": " + "; ".join(bad) if bad else
                               ": every match fires %d cycles after its STOP, no near miss fires" % lat))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
