"""Quantities with dimensions, and the time-to-tick rounding rules.

A quantity is an exact rational times seconds**dim: dim 1 is a time, -1 a
frequency, 0 a ratio or a count. Adding needs equal dimensions (E102),
multiplying adds them. Ticks appear only through the rounding helpers, so every
rounding direction is visible at its call site.
"""
import math
from dataclasses import dataclass
from fractions import Fraction as Fr

from .diag import TseError


@dataclass(frozen=True)
class Q:
    v: Fr
    dim: int

    def add(self, o, span, sign=1):
        if self.dim != o.dim:
            raise TseError("E102", f"cannot {'add' if sign > 0 else 'subtract'} "
                           f"{dimname(self.dim)} and {dimname(o.dim)}", span)
        return Q(self.v + sign * o.v, self.dim)

    def mul(self, o):
        return Q(self.v * o.v, self.dim + o.dim)

    def div(self, o, span):
        if o.v == 0:
            raise TseError("E105", "division by zero", span)
        return Q(self.v / o.v, self.dim - o.dim)

    def ticks(self, fclk):
        """Exact (unrounded) ticks of a time at clock fclk in Hz."""
        return self.v * fclk


def dimname(d):
    return {1: "time", -1: "frequency", 0: "ratio"}.get(d, f"s^{d}")


BASE_UNITS = {
    "s": Q(Fr(1), 1), "ms": Q(Fr(1, 10**3), 1), "us": Q(Fr(1, 10**6), 1), "ns": Q(Fr(1, 10**9), 1),
    "Hz": Q(Fr(1), -1), "kHz": Q(Fr(10**3), -1), "MHz": Q(Fr(10**6), -1),
    "ppm": Q(Fr(1, 10**6), 0),
}
TYPE_DIM = {"time": 1, "freq": -1, "ratio": 0, "count": 0}
DATA_TYPES = ("bit", "bool", "bits", "uint")


# Rounding rules. Each takes exact ticks.
def lower_bound(t):
    """Window lower bound, guard c >= T, minimum hold: round up, never act early."""
    return math.ceil(t)


def upper_bound(t):
    """Window upper bound, invariant c <= T, deadline: round down, never act late."""
    return math.floor(t)


def placement(t):
    """Target of `for T` or `each P`: nearest tick; the error is charged to the tolerance."""
    return round(t)


def drift_budget(tol, elapsed, ppm):
    """Tolerance left after our clock may drift ppm against the other side's over elapsed ticks."""
    return tol - Fr(ppm, 10**6) * elapsed
