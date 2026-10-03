"""Exact cycle counts of TSE lane code under the fixed-slot fetch rule.

A path is the list of instruction addresses a lane executes, in order. path()
gives the dispatch cycle of every entry; block() is a straight-line path;
loop() repeats a path whose last instruction goes back to its first and
returns the iteration lengths, which settle into a repeating pattern. The
timing depends on the lane, on the cycle the first instruction dispatches
(its phase in the slot frame and the data frame) and on the fetch buffer at
that point: State carries both, reset_state() gives the state at RUN.

The rule: an instruction dispatched in cycle c lasts d cycles (isa.cost, or
isa.dslot_cycles for LDW and STW). If the successor's word is buffered it
dispatches in c + d; otherwise the lane reads it in its first own slot
s >= c, replacing the least recently used word, and the successor dispatches
in max(c + d, s + fetch_latency).

WAIT and blocking PUSH or PULL depend on data: pass waits[pc] = k, the cycle
of the instruction in which its end is seen (1 when it ends at once). Pure
functions, standard library only.
"""
from typing import NamedTuple

from . import isa


class TimingError(ValueError):
    pass


class State(NamedTuple):
    cycle: int          # dispatch cycle of the next instruction
    tags: tuple         # buffered word addresses, most recently used first


class PathTiming(NamedTuple):
    dispatch: list      # dispatch cycle of each path entry
    end: State          # when (and with which buffer) the successor of the last entry dispatches

    @property
    def cycles(self):
        return self.end.cycle - self.dispatch[0]


class LoopTiming(NamedTuple):
    first: list         # lengths of the iterations before the repeating part
    period: list        # one repetition of the steady iteration lengths

    def iteration(self, i):
        """Length of iteration i (0 is the first), head dispatch to head dispatch."""
        if i < len(self.first):
            return self.first[i]
        return self.period[(i - len(self.first)) % len(self.period)]

    def total(self, n):
        """Cycles of the first n iterations."""
        return sum(self.iteration(i) for i in range(n))


def reset_state(lane, start_pc, fetch_latency=isa.FETCH_LATENCY):
    """State at RUN: the first word is read in the lane's first own slot."""
    return State(isa.first_slot(0, lane) + fetch_latency, (start_pc >> 1,))


def _check_successor(ins, pc, npc):
    f = ins.fields
    if ins.name == "JMP":
        ok = {f["target"]} if f["cond"] == isa.ENUMS["cond"]["ALWAYS"] else {f["target"], (pc + 1) & 0xFF}
    elif ins.name == "MOV" and f["dst"] == isa.ENUMS["movdst"]["PC"]:
        return
    else:
        ok = {(pc + 1) & 0xFF}
    if npc not in ok:
        raise TimingError(f"pc {pc}: {ins.name} cannot continue at {npc}")


def path(words, pcs, lane, state, exit_pc=None, waits=None,
         fetch_latency=isa.FETCH_LATENCY, entries=isa.FETCH_ENTRIES):
    """Dispatch cycles along pcs, starting with pcs[0] dispatching in state.cycle.

    words maps a pc to its 16-bit word (a list or a dict). exit_pc is the
    successor of the last entry (default: the next address).
    """
    waits = waits or {}
    c, tags = state.cycle, list(state.tags)
    dispatch = []
    for i, pc in enumerate(pcs):
        ins = isa.decode(words[pc])
        if ins is None:
            raise TimingError(f"pc {pc}: illegal word 0x{words[pc]:04X}")
        if i + 1 < len(pcs):
            npc = pcs[i + 1]
        else:
            npc = (pc + 1) & 0xFF if exit_pc is None else exit_pc
        _check_successor(ins, pc, npc)
        co = isa.cost(ins)
        if co.dslot:
            d = isa.dslot_cycles(co.dslot, c, lane, fetch_latency)
        elif co.wait:
            if pc not in waits:
                raise TimingError(f"pc {pc}: {ins.name} waits on data, give waits[{pc}]")
            d = co.base + waits[pc] - 1
        else:
            d = co.base
        dispatch.append(c)
        w = npc >> 1
        if w in tags:
            tags.remove(w)
            c += d
        else:
            s = isa.first_slot(c, lane)
            del tags[entries - 1:]
            c = max(c + d, s + fetch_latency)
        tags.insert(0, w)
    return PathTiming(dispatch, State(c, tuple(tags)))


def block(words, start_pc, n, lane, state, **kw):
    """path() over the n instructions from start_pc."""
    return path(words, [(start_pc + i) & 0xFF for i in range(n)], lane, state, **kw)


def loop(words, pcs, lane, state, waits=None,
         fetch_latency=isa.FETCH_LATENCY, entries=isa.FETCH_ENTRIES):
    """Iteration lengths of a loop over pcs (the last entry goes back to pcs[0]).

    The timing of an iteration depends only on its start cycle modulo the data
    frame and the buffer, so the lengths repeat once that pair repeats.
    """
    seen, lengths = {}, []
    st = state
    while True:
        key = (st.cycle % isa.DATA_FRAME, st.tags)
        if key in seen:
            return _shortest(lengths[:seen[key]], lengths[seen[key]:])
        seen[key] = len(lengths)
        r = path(words, pcs, lane, st, exit_pc=pcs[0], waits=waits,
                 fetch_latency=fetch_latency, entries=entries)
        lengths.append(r.end.cycle - st.cycle)
        st = r.end


def _shortest(first, period):
    """The same sequence of lengths with the shortest prefix and repeating part."""
    while first and first[-1] == period[-1]:
        first, period = first[:-1], period[-1:] + period[:-1]
    n = len(period)
    p = next(p for p in range(1, n + 1) if n % p == 0 and period == period[:p] * (n // p))
    return LoopTiming(first, period[:p])
