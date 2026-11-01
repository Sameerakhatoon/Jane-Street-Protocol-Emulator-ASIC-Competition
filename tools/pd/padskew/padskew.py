"""tools/pd/padskew/padskew.py <corner report>...: tile-internal pin delays from paths.tcl output.

Both are measured from the clock edge at the tile's clk pin, in ns:
  output: clock edge to the change at the port (data arrival of the clock-to-port path);
  input:  data delay from the port to the capturing flop minus the clock delay to that flop,
          so the pin is sampled that long before the clock edge (tsec's "a sample sees the pin
          a pad earlier"); negative means after the edge.
Skew is the spread between pins of one direction, per corner, over rise, fall, max and min.
The Tiny Tapeout multiplexer and the chip pad ring outside the tile are not in these numbers.
"""
import re
import sys


def parse(path):
    pins = {}
    for block in open(path).read().split("PADPATH ")[1:]:
        head, _, body = block.partition("\n")
        if head.startswith("end") or "data arrival time" not in body:
            continue
        kind, pin, _pd, _rf = head.split()
        arrival = float(re.search(r"^\s+([-\d.]+)\s+data arrival time", body, re.M).group(1))
        if kind == "out":
            delay = arrival
        else:
            capture = body.split("data arrival time", 1)[1]
            edge = float(re.search(r"^\s+[-\d.]+\s+([-\d.]+)\s+clock \S+ \((?:rise|fall) edge\)", capture, re.M).group(1))
            clk = float(re.search(r"^\s+[-\d.]+\s+([-\d.]+) [v^] \S+/(?:CLK|GATE\w*|A_CLK)\b", capture, re.M).group(1))
            delay = arrival - (clk - edge)
        pins.setdefault((kind, pin), []).append(delay)
    return pins


def summary(path):
    pins = parse(path)
    lines = []
    for kind in ("in", "out"):
        ds = {p: (min(v), max(v)) for (k, p), v in pins.items() if k == kind}
        if not ds:
            continue
        lo = min(ds.items(), key=lambda kv: kv[1][0])
        hi = max(ds.items(), key=lambda kv: kv[1][1])
        lines.append(f"{kind:3} {len(ds):2} pins  min {lo[1][0]:7.3f} ({lo[0]})  max {hi[1][1]:7.3f} ({hi[0]})  "
                     f"skew {hi[1][1] - lo[1][0]:6.3f} ns")
    return lines


for path in sys.argv[1:]:
    print(path.rsplit("/", 1)[-1].removesuffix(".txt"))
    for line in summary(path):
        print("  " + line)
