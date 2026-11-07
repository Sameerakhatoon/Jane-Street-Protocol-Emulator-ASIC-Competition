# SPDX-License-Identifier: Apache-2.0
"""Cross-check the compiler's timing claim against the pins.

tsec reports, for the transmitter, the cycle at which each edge of a frame
issues relative to the start edge, its ideal instant and the error. This
reads the same edges out of the pins-only VCD with the judges' VCD reader
and UART decoder (code that shares nothing with the compiler): for every
edge of every decoded frame, its offset from the start edge in ticks and
its bit number. It passes when every measured offset equals the compiler's
"at" value for that bit within half a tick, and prints both worst-case
errors and both bit periods (least-squares slope of offset over bit number,
over the same edges: a bit number counts only where the data has an edge).

    python3 examples/uart/timing.py --report build/uart/uart.report.txt --role tx_side \
        --vcd t.vcd --pin uo_out0 --from 3e-4 --baud 115200
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from verif.judges import uart  # noqa: E402
from verif.judges.common import fit_slope  # noqa: E402
from verif.judges.vcd import read_vcd  # noqa: E402

FCLK = 60e6
ROW = re.compile(r"line\s+\d+\s+(drive|shift)\s+\w+\s+#(\d+): at\s+(\d+), ideal\s+([\d.]+), "
                 r"error\s+([\d.]+) <= ([\d.]+)")


def compiler_edges(path, role):
    """[(at, ideal, error, budget)] per bit of the frame, from the report."""
    rows = []
    inside = False
    with open(path) as fh:
        for ln in fh:
            if ln.startswith("== role "):
                inside = ln.split()[2] == role
                continue
            m = ROW.search(ln) if inside else None
            if m:
                rows.append(tuple(float(m.group(i)) for i in (3, 4, 5, 6)))
    if not rows:
        raise SystemExit("no edge rows for role %s in %s" % (role, path))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", required=True)
    ap.add_argument("--role", default="tx_side")
    ap.add_argument("--vcd", required=True)
    ap.add_argument("--pin", required=True)
    ap.add_argument("--from", dest="t_from", type=float, default=0.0)
    ap.add_argument("--baud", type=float, required=True)
    a = ap.parse_args()

    want = compiler_edges(a.report, a.role)
    bit = FCLK / a.baud
    c_err = max(r[2] for r in want)
    c_bud = min(r[3] for r in want)

    tr = read_vcd(a.vcd, {"rx": a.pin})
    sig = tr["rx"]
    sig.crop(int(round(tr.units(a.t_from))))
    res = uart.decode(tr, a.baud)
    T = tr.units(1.0 / a.baud)
    pts = []
    worst = 0.0
    bad = 0
    for f in res.frames:
        for e in sig.edges(f.t0, f.t0 + 9.5 * T):
            k = int(round((e - f.t0) / T))
            off = tr.seconds(e - f.t0) * FCLK
            pts.append((k, off))
            worst = max(worst, abs(off - k * bit))
            if k >= len(want) or abs(off - want[k][0]) > 0.5:
                bad += 1
    if not pts:
        print("FAIL timing: no frames on %s" % a.pin)
        return 1
    m_per = fit_slope(pts)
    c_per = fit_slope([(k, want[k][0]) for k, _ in pts if k < len(want)])
    print("compiler: worst edge error %.2f ticks (budget %.2f), bit period %.3f ticks = %.0f baud"
          % (c_err, c_bud, c_per, FCLK / c_per))
    print("judge:    worst edge error %.2f ticks over %d frames, %d edges, bit period %.3f ticks = %.0f baud (%+.3f%%)"
          % (worst, len(res.frames), len(pts), m_per, FCLK / m_per, (bit / m_per - 1) * 100))
    if bad or abs(worst - c_err) > 0.5:
        print("FAIL timing: %d edges off the compiler's schedule" % bad)
        return 1
    print("PASS timing: every edge at the compiler's cycle, worst error %.2f of %.2f ticks" % (worst, c_bud))
    return 0


if __name__ == "__main__":
    sys.exit(main())
