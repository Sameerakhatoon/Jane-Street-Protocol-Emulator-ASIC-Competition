# SPDX-License-Identifier: Apache-2.0
"""Pad delays between the pins and the lane, per direction.

An input pad delays a pin change on its way to the lane's synchronizer; an output pad delays a
drive on its way from the lane to the pin. Each direction has a range (least, largest delay)
and a skew, the largest delay difference between two pads of that direction, all in ns.

The timing analysis counts one pad on a path:
  - a sample after an input edge (both pins inputs): the input range;
  - a distance between two of our own outputs, or two placed drives: the output skew;
  - two input events seen by the lane, or two placed samples: the input skew;
  - a placed drive against a placed sample, or a hold from our drive to a sample: the skew
    between an input and an output pad (cross), at least the output skew for a hold;
  - a path through both directions (an output answering an input edge, a sample after our own
    output edge): one pad in the hull of the two ranges. This is the single-range model the
    analysis had before the directions were split, kept as is so that equal ranges give the
    same results; physically such a path crosses an input pad and an output pad, so it is only
    as sound as that model (the gap is at most the smaller of the two largest delays).
"""
from fractions import Fraction as Fr


class Pads:
    """inp, out: (least, largest, skew) in ns. cross: the largest delay difference between an
    input pad and an output pad. split: the directions were given apart (diagnostics then name
    the per-direction flags)."""

    def __init__(self, inp, out, cross=None, split=False):
        self.inp, self.out, self.split = tuple(inp), tuple(out), split
        if cross is None:
            cross = self.inp[2] if self.inp == self.out else self.cross_bound()
        self.cross = cross

    @classmethod
    def of(cls, p):
        """A Pads, or one range for every pad: (least, largest[, skew]), skew default largest - least."""
        if isinstance(p, Pads):
            return p
        rng = (p[0], p[1], p[2] if len(p) > 2 else p[1] - p[0])
        return cls(rng, rng)

    @classmethod
    def from_flags(cls, lo, hi, skew, inp=(None, None, None), out=(None, None, None)):
        """The driver's flags: the single range (skew None: largest - least) and per-direction
        (least, largest, skew), each None when not given. A direction that is not given at all
        is the single range. A given direction's skew defaults to its width, or to --pad-skew
        when that is given and smaller (it bounds any two pads). The skew between an input and
        an output pad is what the two ranges allow, or --pad-skew when that is given and smaller."""
        single = (lo, hi, hi - lo if skew is None else skew)

        def side(d):
            if d == (None, None, None):
                return single
            a = lo if d[0] is None else d[0]
            b = hi if d[1] is None else d[1]
            s = d[2] if d[2] is not None else (b - a if skew is None else min(skew, b - a))
            return (a, b, s)
        i, o = side(tuple(inp)), side(tuple(out))
        split = i != single or o != single
        pads = cls(i, o, split=split)
        if i != o and skew is not None:
            pads.cross = min(skew, pads.cross)
        return pads

    def cross_bound(self):
        return max(self.out[1] - self.inp[0], self.inp[1] - self.out[0])

    def hull(self):
        """(least, largest) over both directions: one pad on a path through both."""
        if self.inp[:2] == self.out[:2]:
            return self.inp[:2]
        return min(self.inp[0], self.out[0]), max(self.inp[1], self.out[1])

    def window(self, kind):
        """(least, largest) ns the pads add to a window measured from an input edge: a sample
        ("in") has only input pads on its path, an output ("out") one in the hull."""
        return self.inp[:2] if kind == "in" else self.hull()

    def skew(self, a, b):
        """Largest delay difference between a pad of kind a and one of kind b ("in" or "out")."""
        if a == b:
            return self.inp[2] if a == "in" else self.out[2]
        return self.cross

    def skew_flag(self, a, b):
        if not self.split:
            return "--pad-skew"
        return "--pad-in-skew" if a == b == "in" else "--pad-out-skew" if a == b == "out" else "--pad-skew"

    def ticks_by_kind(self, fclk):
        """{"in": (least, largest), "out": (least, largest)} in ticks, as window() gives them."""
        return {k: ticks(self.window(k), fclk) for k in ("in", "out")}


def ticks(rng, fclk):
    """ns to ticks, per element."""
    return tuple(Fr(p) * fclk / 10**9 for p in rng)
