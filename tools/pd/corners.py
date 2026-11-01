#!/usr/bin/env python3
"""tools/pd/corners.py: three-corner timing table from a LibreLane metrics.json.

usage: corners.py runs/wokwi/final/metrics.json [--require typ,slow,fast] [--period 16.67]
Exit status 1 if setup or hold worst slack is negative at any required corner.
"""
import argparse
import json
import os
import sys

CORNERS = {
    "typ": "nom_typ_1p20V_25C",
    "slow": "nom_slow_1p08V_125C",
    "fast": "nom_fast_1p32V_m40C",
}
FIELDS = [
    ("setup ws", "timing__setup__ws"),
    ("setup tns", "timing__setup__tns"),
    ("setup vio", "timing__setup_vio__count"),
    ("hold ws", "timing__hold__ws"),
    ("hold vio", "timing__hold_vio__count"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("metrics")
    ap.add_argument("--require", default="typ,slow,fast")
    ap.add_argument("--period", type=float, default=None,
                    help="CLOCK_PERIOD in ns (default: from the run's resolved.json)")
    args = ap.parse_args()
    with open(args.metrics) as f:
        m = json.load(f)
    period = args.period
    if period is None:
        resolved = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(args.metrics))), "resolved.json")
        with open(resolved) as f:
            period = float(json.load(f)["CLOCK_PERIOD"])

    print(f"{'corner':6}" + "".join(f"{name:>11}" for name, _ in FIELDS) + f"{'fmax MHz':>10}")
    failed = []
    for short, full in CORNERS.items():
        row = [m.get(f"{key}__corner:{full}") for _, key in FIELDS]
        cells = "".join(f"{'n/a' if v is None else round(v, 3):>11}" for v in row)
        ws = row[0]
        # Worst path is about period - slack long, so this is the clock it could run at.
        fmax = "" if ws is None else f"{1000.0 / (period - ws):.1f}"
        print(f"{short:6}{cells}{fmax:>10}")
        if short in args.require.split(","):
            if row[0] is None or row[3] is None:
                failed.append(f"{short}: metric missing")
            elif row[0] < 0 or row[3] < 0:
                failed.append(f"{short}: setup {row[0]:.3f} ns, hold {row[3]:.3f} ns")
    for line in failed:
        print("FAIL", line)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
