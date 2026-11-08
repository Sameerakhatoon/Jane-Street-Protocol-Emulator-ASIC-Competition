# SPDX-License-Identifier: Apache-2.0
"""place_lemmas.py TOP LOG [MARGIN [SLOWLOG]]: give a chain's place harness (top_<role>_place.sv) the lemmas
a simulation suggests. LOG holds place_tb.sv's LEMLOG lines (lem CHAIN ENTRY PC Q PC_Q): q, the cycles
since the chain's first action plus the sequencer's delay count, at every cycle a chain is open.
Each (chain, entry, pc) seen gets "q in [least - MARGIN, most + MARGIN]" in lem_ok, which every
task asserts with its obligation. A lemma is a claim to prove, not an assumption: one a run never
showed false but the lane can break fails the proof, and a combination the run never reached is
left free, as is every wait that ends a chain (the harness lists them) and the cycle after it.
SLOWLOG, from a run whose host is slow to read (place_tb.sv RXRE): a (chain, entry, pc) whose q
range differs there waits on the host somewhere in its chain, so it gets no lemma either.
TOP is rewritten in place."""
import re
import sys
from collections import defaultdict


def ranges(log, ends):
    seen = defaultdict(lambda: [None, None])
    for line in open(log):
        m = re.match(r"lem (\d+) (\d+) (\d+) (\d+) (\d+)$", line)
        if m:
            c, k, pc, q, pc_q = map(int, m.groups())
            if pc in ends or pc_q in ends:      # the cycle after such a wait still counts it
                continue
            r = seen[(c, k, pc)]
            r[0] = q if r[0] is None else min(r[0], q)
            r[1] = q if r[1] is None else max(r[1], q)
    return seen


def main(top, log, margin=0, slow=None):
    text = open(top).read()
    marker = "  assign lem_ok = 1'b1;                  // LEMMAS\n"
    if marker not in text:
        sys.exit(f"{top}: no lemma marker (no chain, or lemmas already in)")
    cb = int(re.search(r"logic \[(\d+):0\] pch = ", text)[1]) + 1
    qw = int(re.search(r"logic \[(\d+):0\] q;", text)[1]) + 1
    # at a wait that ends a chain the lane waits for the outside world, which the solver leaves
    # free: q there grows as long as it takes, so no lemma
    ends = {int(x) for x in re.search(r"// chains end at:([ \d]*)\n", text)[1].split()}
    seen = ranges(log, ends)
    if not seen:
        sys.exit(f"{log}: no lem lines")
    dropped = 0
    if slow:
        other = ranges(slow, ends)
        keep = {key: r for key, r in seen.items() if other.get(key, r) == r}
        dropped = len(seen) - len(keep)
        seen = keep
    w = cb + 16
    top_q = (1 << qw) - 1
    lines = [f"  // lemmas from a simulation (place_lemmas.py, margin {margin}): q per chain, entry, pc",
             "  always_comb begin", "    lem_ok = 1'b1;",
             "    if (!(" + " || ".join(f"pc_q == 8'd{e}" for e in sorted(ends)) + ")) case ({pch, pk, pc})"
             if ends else "    case ({pch, pk, pc})"]
    for (c, k, pc), (lo, hi) in sorted(seen.items()):
        lo, hi = max(0, lo - margin), min(top_q, hi + margin)
        lines.append(f"      {w}'d{(c << 16) | (k << 8) | pc}: lem_ok = q >= {qw}'d{lo} && q <= {qw}'d{hi};")
    lines += ["      default: ;", "    endcase", "  end"]
    open(top, "w").write(text.replace(marker, "\n".join(lines) + "\n"))
    print(f"{top}: {len(seen)} lemmas" + (f", {dropped} left free (host timing)" if slow else ""))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 0,
         sys.argv[4] if len(sys.argv) > 4 else None)
