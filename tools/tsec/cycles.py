"""The lane cycle model: the one place tsec computes how long instructions take.

Every analysis in tsec (the frame analysis in timing.py, the state-machine
analysis in fsmtime.py, the self-test) asks this module, through step(), when
the next instruction dispatches. It holds the instruction durations and the
two-word fetch buffer rule with fixed SRAM slots: a successor in a buffered
word dispatches at c + d, otherwise its word is fetched in the lane's first
slot s >= c and it dispatches at max(c + d, s + fetch_latency).

When the ISA layer publishes its exact cycle-count API, step() becomes a call
into it and nothing else in the compiler changes.
"""
from dataclasses import dataclass

from . import isa


@dataclass(frozen=True)
class FetchModel:
    S: int = isa.FRAME_SLOTS          # slot frame: lane k owns SRAM cycles s with s % S == k
    entries: int = isa.FETCH_ENTRIES  # words kept per lane, least recently used replaced
    lat: int = isa.FETCH_LATENCY      # fetch_latency: 1 decode from A_DOUT, 2 registered A_DOUT


def first_slot(c, period, k):
    return c + ((k - c) % period)


def base_duration(op, a, c, lane, fm):
    """Cycles of a non-WAIT instruction dispatched in cycle c (blocking PUSH and PULL: no wait)."""
    if op in ("MOV", "OUT", "IN", "TMR", "EMIT"):
        return 1 + a.get("delay", 0)
    if op == "DLY":
        return 1 + a["d"]
    if op == "CSRW":
        return 3 if a["addr"] in isa.CLASS_L else 1
    if op in ("STW", "LDW"):
        s = first_slot(c, isa.DATA_SLOT_PERIOD, 4 * lane + 3)
        return s + 1 - c if op == "STW" else s + 2 - c + fm.lat - 1
    return 1


def next_dispatch(c, d, npc, buf, lane, fm):
    """Two-word buffer fetch rule; buf[0] is the current word. Updates buf in place."""
    w = npc // 2
    if w in buf:
        buf.remove(w)
        buf.insert(0, w)
        return c + d
    s = first_slot(c, fm.S, lane)
    del buf[fm.entries - 1:]
    buf.insert(0, w)
    return max(c + d, s + fm.lat)


def step(op, a, c, npc, buf, lane, fm, end=None):
    """Dispatch cycle of the successor npc of an instruction dispatched in cycle c.
    For a WAIT (and a blocking PUSH or PULL), end is the cycle its condition is first seen."""
    if end is not None:
        d = end - c + 1 + (a.get("delay", 0) if op == "WAIT" else 0)
    else:
        d = base_duration(op, a, c, lane, fm)
    return next_dispatch(c, d, npc, buf, lane, fm)
