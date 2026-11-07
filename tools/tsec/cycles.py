# SPDX-License-Identifier: Apache-2.0
"""The lane cycle model seam: the one place tsec asks how long instructions take.

Every analysis in tsec (the frame analysis in timing.py, the state-machine
analysis in fsmtime.py, the self-test) asks step() when the next instruction
dispatches. step() encodes the instruction and hands it to the ISA layer's
exact cycle-count API (tools.tseasm.timing), so the compiler, the assembler
and the sequencer specification share one set of durations and one fetch
rule. Nothing in this module computes a duration itself.

The slot period S is part of the ISA table; the buffer size and the fetch
latency stay parameters of the analysis, as the timing API allows.
"""
from dataclasses import dataclass
from functools import lru_cache

from . import isa
from ..tseasm import isa as tisa
from ..tseasm import timing as ttiming


@dataclass(frozen=True)
class FetchModel:
    S: int = isa.FRAME_SLOTS          # slot frame: lane k owns SRAM cycles s with s % S == k
    entries: int = isa.FETCH_ENTRIES  # words kept per lane, least recently used replaced
    lat: int = isa.FETCH_LATENCY      # fetch_latency: 1 decode from A_DOUT, 2 registered A_DOUT

    def __post_init__(self):
        if self.S != isa.FRAME_SLOTS:
            raise ValueError(f"the slot period is fixed by the ISA table at {isa.FRAME_SLOTS}")


def word(op, a, npc):
    """The 16-bit word of an IR instruction; a jump gets npc as its target (its cost does not
    depend on the target, and the timing API checks that npc is a legal successor)."""
    return _word(op, tuple(a.items()), npc % isa.MAX_INSTRUCTIONS if op == "JMP" else 0)


@lru_cache(maxsize=None)
def _word(op, items, target):
    f = {k: v for k, v in items if k != "expect_taken"}
    if op == "JMP":
        f["target"] = target
    return isa.encode(op, f)


def start_cycle(lane, fm):
    """Dispatch cycle of the first instruction after RUN."""
    return ttiming.reset_state(lane, 0, fm.lat).cycle


def duration(op, a, c, lane, fm):
    """Cycles of a non-waiting instruction dispatched in cycle c, fetch stalls excluded."""
    co = tisa.cost(tisa.decode(word(op, a, 0)))
    if co.dslot:
        return tisa.dslot_cycles(co.dslot, c, lane, fm.lat)
    return co.base


def step(op, a, c, pc, npc, buf, lane, fm, end=None):
    """Dispatch cycle of the successor npc of the instruction at pc dispatched in cycle c; buf
    (buffered word addresses, most recently used first) is updated in place. For a WAIT or a
    blocking PUSH or PULL, end is the cycle its condition is first seen (default: at once)."""
    n = isa.MAX_INSTRUCTIONS
    k = 1 if end is None else end - c + 1
    d, tags = _path(word(op, a, npc), pc % n, npc % n, c % tisa.DATA_FRAME, tuple(buf), k, lane, fm)
    buf[:] = tags
    return c + d


@lru_cache(maxsize=1 << 16)
def _path(w, pc, npc, phase, tags, k, lane, fm):
    """The timing API on one instruction. Its result depends on the dispatch cycle only through
    the phase in the data frame (a multiple of the slot frame), so it is computed once per phase."""
    r = ttiming.path({pc: w}, [pc], lane, ttiming.State(phase, tags), exit_pc=npc, waits={pc: k},
                     fetch_latency=fm.lat, entries=fm.entries)
    return r.end.cycle - phase, r.end.tags
