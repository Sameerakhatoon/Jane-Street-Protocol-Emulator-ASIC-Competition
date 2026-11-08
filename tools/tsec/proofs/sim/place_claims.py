# SPDX-License-Identifier: Apache-2.0
"""place_claims.py TABLE LOG: every distance a place harness logged in simulation (PLACE_LOG)
against its window and the analysis' claim (TABLE: <role>_place.txt). A distance outside its
window is a timing failure; one outside the claim means the harness and the analysis disagree
on what they measure. A claim can be exceeded only by a least distance (no upper bound), whose
analysis stops counting at a wait for the outside world. Exit 1 on either kind of failure."""
import re
import sys
from collections import defaultdict


def main(table, log):
    win, claim = {}, {}
    for line in open(table):
        m = re.match(r"\s+g(\d+) k(\d+) \[(\d+), (\d*)\](?: claim \[(-?\d+), (-?\d+)\])?", line)
        if m:
            key = (int(m[1]), int(m[2]))
            win[key] = (int(m[3]), int(m[4]) if m[4] else None)
            if m[5] is not None:
                claim[key] = (int(m[5]), int(m[6]))
    seen = defaultdict(set)
    for line in open(log):
        m = re.match(r"place g(\d+) k(\d+) (\d+)", line)
        if m:
            seen[(int(m[1]), int(m[2]))].add(int(m[3]))
    bad = 0
    for key in sorted(seen):
        lo, hi = win[key]
        c = claim.get(key)
        vals = sorted(seen[key])
        out_w = [v for v in vals if v < lo or (hi is not None and v > hi)]
        out_c = [v for v in vals if c is not None and (v < c[0] or (hi is not None and v > c[1]))]
        if out_w or out_c:
            bad += 1
            print(f"g{key[0]} k{key[1]}: seen {vals}, window [{lo}, {hi}], claim {c}")
    print(f"{len(seen)} entries seen, {bad} outside their window or claim")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:3]))
