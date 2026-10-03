"""SPI judge: words on MOSI and MISO framed by chip select.

Written from the Motorola SPI definition. CPOL is the clock level while
idle; CPHA=0 samples data on the first (leading) clock edge after chip
select asserts, CPHA=1 on the second (trailing) edge. Mode = CPOL*2 + CPHA,
so modes 0 and 3 sample on the rising edge and modes 1 and 2 on the
falling edge. A frame is one chip-select assertion (the whole trace when
no CS is mapped); its key lists the words of each mapped data line.
Checks: the clock is at its idle level when CS asserts and deasserts, every
frame holds whole words, and optional setup and hold times around each
sampling edge.
"""
from .common import Result
from .vcd import X

ROLES = ("clk", "mosi", "miso", "cs")
REQUIRED = ("clk",)
PULLUP = False
OPTS = ("mode", "bits", "lsb_first", "cs_high", "tsu", "thd")


def add_args(p):
    p.add_argument("--mode", type=int, default=0, choices=range(4))
    p.add_argument("--bits", type=int, default=8)
    p.add_argument("--lsb-first", action="store_true")
    p.add_argument("--cs-high", action="store_true", help="chip select is active high")
    p.add_argument("--tsu", type=float, default=0.0, help="data setup before the sampling edge, seconds")
    p.add_argument("--thd", type=float, default=0.0, help="data hold after the sampling edge, seconds")


def decode(trace, mode=0, bits=8, lsb_first=False, cs_high=False, tsu=0.0, thd=0.0):
    res = Result(trace)
    clk = trace["clk"]
    cpol, cpha = mode >> 1, mode & 1
    samp = 1 if cpol == cpha else 0       # level the clock goes to on a sampling edge
    lines = [r for r in ("mosi", "miso") if r in trace]
    for a, b in _windows(trace, cs_high):
        if clk.at(a) != cpol:
            res.violate(a, "clock idle at CS assert", clk.at(a), cpol, text="CPOL mismatch")
        edges = clk.edges(a, b, samp)
        if b < trace.end and clk.before(b) != cpol:
            res.violate(b, "clock idle at CS deassert", clk.before(b), cpol, text="CPOL mismatch")
        parts = []
        for role in lines:
            sig = trace[role]
            words, cur, nbit = [], 0, 0
            for k, te in enumerate(edges):
                v = sig.before(te)
                if v == X:
                    res.violate(te, role + " unknown level", "X", "0 or 1")
                    v = 0
                cur = cur | (v << nbit) if lsb_first else (cur << 1) | v
                nbit += 1
                if nbit == bits:
                    words.append(cur)
                    cur, nbit = 0, 0
                _setup_hold(res, sig, role, te, edges[k + 1] if k + 1 < len(edges) else b, tsu, thd)
            if nbit:
                res.violate(b, role + " incomplete word", "%d bits" % nbit, "%d bits" % bits)
            parts.append("%s=%s" % (role, ",".join("%0*X" % ((bits + 3) // 4, w) for w in words)))
        res.frame(a, b, " ".join(parts) if parts else "%d edges" % len(edges))
    return res.sort()


def _setup_hold(res, sig, role, te, t_next, tsu, thd):
    if tsu > 0:
        tc = sig.last_change_before(te)
        if tc is not None:
            res.check_min(te, role + " tSU", te - tc, tsu)
    if thd > 0:
        i = sig.first_at_or_after(te)
        if i < len(sig) and sig.times[i] < t_next:
            res.check_min(te, role + " tHD", sig.times[i] - te, thd)


def _windows(trace, cs_high):
    if "cs" not in trace:
        return [(0, trace.end)]
    cs = trace["cs"]
    act = 1 if cs_high else 0
    out = []
    start = 0 if cs.at(0) == act else None
    for t, v in zip(cs.times, cs.levels):
        if v == act and start is None:
            start = t
        elif v != act and start is not None:
            if t > start:
                out.append((start, t))
            start = None
    if start is not None:
        out.append((start, trace.end))
    return out
