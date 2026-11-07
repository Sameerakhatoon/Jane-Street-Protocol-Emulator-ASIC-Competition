# SPDX-License-Identifier: Apache-2.0
"""UART judge: asynchronous serial characters on one or two lines.

Written from the definition of an asynchronous character: the line idles
at mark (1); a character is a start bit at space (0), 5 to 9 data bits
least significant first, an optional parity bit (even, odd, mark or space)
and 1, 1.5 or 2 stop bits at mark. Like a receiver, the judge times every
sample from the start edge and samples the middle of each bit. It also
fits every edge of the character to a bit grid, which measures the real
baud rate and catches one bit that is longer or shorter than the others.
Each frame key is the character in hex, or BREAK for a character of all
zeros without its stop bit.
"""
from .common import Result, parity_ok, rate_check
from .vcd import X

ROLES = ("rx", "tx")
REQUIRED = ()
PULLUP = False
OPTS = ("baud", "bits", "parity", "stop", "invert", "msb_first", "baud_tol", "edge_tol")


def add_args(p):
    p.add_argument("--baud", type=float, required=True)
    p.add_argument("--bits", type=int, default=8, choices=range(5, 10))
    p.add_argument("--parity", default="none", choices=("none", "even", "odd", "mark", "space"))
    p.add_argument("--stop", type=float, default=1.0, choices=(1.0, 1.5, 2.0))
    p.add_argument("--invert", action="store_true", help="line idles low")
    p.add_argument("--msb-first", action="store_true")
    p.add_argument("--baud-tol", type=float, default=2.0, help="allowed baud error, percent")
    p.add_argument("--edge-tol", type=float, default=0.2, help="allowed edge offset, fraction of a bit")


def decode(trace, baud, bits=8, parity="none", stop=1.0, invert=False, msb_first=False,
           baud_tol=2.0, edge_tol=0.2):
    res = Result(trace)
    roles = [r for r in ROLES if r in trace]
    for role in roles:
        _line(res, trace[role], role + " " if len(roles) > 1 else "", trace.units(1.0 / baud),
              baud, bits, parity, stop, int(invert), msb_first, baud_tol, edge_tol)
    return res.sort()


def _line(res, sig, tag, T, baud, bits, parity, stop, inv, msb_first, baud_tol, edge_tol):
    idle = 1 ^ inv
    act = 1 - idle
    for t, v in zip(sig.times, sig.levels):
        if v != X:
            if v != idle:
                res.violate(t, tag + "idle level", v, idle, text="line not idle when first driven")
            break
    nb = 1 + bits + (parity != "none")
    stop_at = {1.0: (0.5,), 1.5: (0.5, 1.25), 2.0: (0.5, 1.5)}[stop]
    width = (bits + 3) // 4
    i = 1
    n = len(sig)
    while i < n:
        if sig.levels[i] != act or sig.levels[i - 1] != idle:
            i += 1
            continue
        t0 = sig.times[i]
        if sig.at(t0 + 0.5 * T) != act:
            res.violate(t0, tag + "start bit", "%d at mid bit" % idle, act, text="glitch, no character")
            i += 1
            continue
        lv = [sig.at(t0 + (k + 0.5) * T) for k in range(1, nb)]
        sv = [sig.at(t0 + (nb + s) * T) for s in stop_at]
        if X in lv or X in sv:
            res.violate(t0, tag + "unknown level", "X", "0 or 1", text="X inside a character")
        lb = [v ^ inv if v != X else 0 for v in lv]
        data = lb[:bits]
        value = 0
        for k, b in enumerate(data):
            value |= b << (bits - 1 - k if msb_first else k)
        if parity != "none":
            p = lb[bits]
            ok = {"even": parity_ok(data + [p], "even"), "odd": parity_ok(data + [p], "odd"),
                  "mark": p == 1, "space": p == 0}[parity]
            if not ok:
                res.violate(t0, tag + "parity", p, parity, text="character %0*X" % (width, value))
        stop_ok = all(v == idle for v in sv)
        brk = not stop_ok and all(b == 0 for b in lb)
        if not stop_ok:
            res.violate(t0 + (nb + 0.5) * T, tag + "stop bit", act, idle,
                        text="break" if brk else "framing error in %0*X" % (width, value))
        mid_stop = t0 + (nb + 0.5) * T
        edges = sig.edges(t0, mid_stop)
        slope = rate_check(res, t0, edges, T, baud_tol, tag + "baud", "baud", baud)
        if slope:
            worst = max([abs(e - t0 - round((e - t0) / slope) * slope) / T for e in edges] or [0])
            if worst > edge_tol:
                res.violate(t0, tag + "bit edge", "%.3f bit" % worst, "<= %g bit" % edge_tol,
                            text="an edge off the bit grid")
        res.frame(t0, t0 + (nb + stop) * T, tag + ("BREAK" if brk else "%0*X" % (width, value)))
        i = sig.first_after(mid_stop)
