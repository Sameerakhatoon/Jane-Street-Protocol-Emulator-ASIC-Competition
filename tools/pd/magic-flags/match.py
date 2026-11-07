# SPDX-License-Identifier: Apache-2.0
# tools/pd/magic-flags/match.py: for each flag, is the same spot flagged on the real layout?
# REAL means the clip of the real GDS has an error touching the flag; ABSTRACT means it has none.
import os

out = os.environ["OUT"]


def touches(a, b, slack=0.05):
    return a[0] <= b[2] + slack and b[0] <= a[2] + slack and a[1] <= b[3] + slack and b[1] <= a[3] + slack


lines, real = [], 0
for line in open(os.path.join(out, "flags.txt")):
    tag, rule, box = line.rstrip("\n").split("|")
    box = [float(x) for x in box.split()]
    hits = []
    with open(os.path.join(out, "clips", tag + ".drc")) as f:
        for err in f:
            why, b = err.rsplit("|", 1)
            if touches(box, [float(x) for x in b.split()]):
                hits.append(why)
    real += bool(hits)
    verdict = "REAL " + "; ".join(sorted(set(hits))) if hits else "ABSTRACT"
    lines.append(f"{tag} {rule} {box} {verdict}")
lines.append(f"{len(lines)} flags: {real} also on the real layout, {len(lines) - real} only on the abstract")
open(os.path.join(out, "summary.txt"), "w").write("\n".join(lines) + "\n")
