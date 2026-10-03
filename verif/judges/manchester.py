"""Manchester judge: bit streams from one line, IEEE 802.3 or G.E. Thomas.

Every bit cell has a transition in its middle; that transition carries
the bit. IEEE 802.3 sends the complement of the bit in the first half and
the bit itself in the second, so a rising middle transition is a 1. The
G.E. Thomas convention is the opposite: a rising middle transition is a 0.
Between two equal bits the line also changes at the cell boundary, so
edges are half a bit or a whole bit apart and nothing else.

A burst is a run of edges with no gap longer than --gap bit times. Each
edge gets a half-bit number counted edge to edge; middle transitions are
the edges of one parity. A gap of a whole bit always ends on a middle
transition, so the first such gap fixes the parity; a burst without one
(all bits equal) is read with its first edge as a middle transition. A
middle position with no edge is a missing transition: the burst is
reported as ERROR from there. The bit rate is fitted over all edges of a
burst, as for UART and CAN.

Frame keys: the bits in order ("0110..."), or with --format lsb or msb
whole bytes in hex sent least or most significant bit first, with any
leftover bits after a "+" ("55D5+101").
"""
from .common import Result, rate_check
from .vcd import X

ROLES = ("line",)
REQUIRED = ("line",)
PULLUP = False
OPTS = ("bitrate", "convention", "fmt", "rate_tol", "gap")


def add_args(p):
    p.add_argument("--bitrate", type=float, required=True)
    p.add_argument("--convention", choices=("ieee", "thomas"), default="ieee",
                   help="ieee: rising middle edge is 1; thomas: rising middle edge is 0")
    p.add_argument("--format", dest="fmt", choices=("bits", "lsb", "msb"), default="bits")
    p.add_argument("--rate-tol", type=float, default=1.0, help="allowed bit rate error, percent")
    p.add_argument("--gap", type=float, default=4.0, help="idle bit times that end a burst")


class Burst:
    __slots__ = ("t0", "t1", "bits", "times", "error")

    def __init__(self, t0):
        self.t0 = t0
        self.t1 = t0
        self.bits = []
        self.times = []         # time of each bit's middle transition
        self.error = False


def bursts(res, sig, T, convention="ieee", rate_tol=1.0, gap=4.0, nominal=None):
    """Split the line into bursts and decode each; violations go to res."""
    half = T / 2.0
    out = []
    times, levels = sig.times, sig.levels
    n = len(times)
    i = 0
    while i < n and levels[i] == X:
        i += 1
    i += 1
    while i < n:
        j = i
        while j + 1 < n and times[j + 1] - times[j] <= gap * T and levels[j + 1] != X:
            j += 1
        out.append(_burst(res, sig, i, j, T, half, convention))
        if nominal and j > i:
            rate_check(res, times[i], list(times[i + 1:j + 1]), half, rate_tol, "bit rate", "bit/s", nominal)
        i = j + 1
        while i < n and levels[i] == X:
            res.violate(times[i], "unknown level", "X", "0 or 1")
            i += 1
    return out


def _burst(res, sig, i, j, T, half, convention):
    times, levels = sig.times, sig.levels
    b = Burst(times[i])
    b.t1 = times[j]
    h = [0]
    for k in range(i + 1, j + 1):
        d = times[k] - times[k - 1]
        steps = int(round(d / half))
        if steps == 0:
            res.violate(times[k - 1], "glitch", "%.3g bit" % (d / T), ">= 0.5 bit")
            steps = 1
        h.append(h[-1] + steps)
    parity = 0
    for k in range(1, len(h)):
        if h[k] - h[k - 1] == 2:
            parity = h[k] & 1
            break
    for k in range(len(h)):
        if k > 0:
            lo, hi = h[k - 1], h[k]
            inside = [m for m in range(lo + 1, hi) if (m & 1) == parity]
            if inside:
                t = times[i + k - 1]
                res.violate(t, "missing transition", "%.3g bit without an edge" % ((hi - lo) / 2.0),
                            "a middle transition every bit")
                b.error = True
                return b
        if (h[k] & 1) == parity:
            after = levels[i + k]
            bit = after if convention == "ieee" else 1 - after
            b.bits.append(bit)
            b.times.append(times[i + k])
    return b


def key_of(bits, fmt):
    if fmt == "bits":
        return "".join(str(x) for x in bits)
    nb = len(bits) // 8
    s = ""
    for k in range(nb):
        chunk = bits[8 * k:8 * k + 8]
        if fmt == "msb":
            chunk = chunk[::-1]
        v = 0
        for j, x in enumerate(chunk):
            v |= x << j
        s += "%02X" % v
    rest = bits[8 * nb:]
    if rest:
        s += "+" + "".join(str(x) for x in rest)
    return s


def decode(trace, bitrate, convention="ieee", fmt="bits", rate_tol=1.0, gap=4.0):
    res = Result(trace)
    T = trace.units(1.0 / bitrate)
    for b in bursts(res, trace["line"], T, convention, rate_tol, gap, nominal=bitrate):
        if b.error:
            res.frame(b.t0, b.t1, "ERROR", "%d bits before the error" % len(b.bits))
        elif b.bits:
            res.frame(b.t0, b.t1, key_of(b.bits, fmt))
    return res.sort()
