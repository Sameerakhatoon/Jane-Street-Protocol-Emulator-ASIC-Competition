"""Paced timelines inside a state machine: `for`, `each` and `~` outside the one-frame shape.

A seq body is a chain of timed actions (rule S4): each action has an ideal
instant measured from the first action of its chain, `for T` (or `each P`)
is the gap to the next action, and `~` its tolerance (rule S5). Here a chain
runs through drive, release, shift and sample statements, counted loops
(unrolled in the timeline, not in the code) and `if` (both branches must
have the same timed shape); push, emit and set take no time. Waiting for
the environment (await, a window, a blocking pull, `wait T`) ends a chain;
a statement with a window starts a new one, so the window's action is the
reference of what follows it. A chain whose statements carry no `for`,
`each` or `~` places nothing and is dropped.

Two actions at the same ideal instant on different wires (a statement
without `for` followed by another) must leave the lane in one instruction:
the first is a drive of a side-set wire (both side-set bits are carried by
every instruction), merged into the second.
"""
import math
from dataclasses import dataclass, field
from fractions import Fraction as Fr

from . import ast as A
from .diag import TseError
from .lower import unsupported

OUT_OFFSET = 3          # an output reaches the pin 3 cycles after its issue, relative to a sample


@dataclass(eq=False)
class Entry:
    stmts: list                 # the statements that may run at this instant (if branches)
    ideal: Fr                   # ticks after the chain's first action
    tol: Fr                     # ticks, before drift
    kind: str                   # out or in
    wires: tuple


@dataclass(eq=False)
class Chain:
    entries: list = field(default_factory=list)
    paced: bool = False         # some statement has for, each or ~

    def index(self, stmt):
        return [k for k, e in enumerate(self.entries) if any(s is stmt for s in e.stmts)]


def issue(e):
    """Issue cycle of an entry's action on the chain's clock, rounded to the nearest cycle: an
    output reaches the pin OUT_OFFSET cycles after its issue, relative to a sample."""
    return math.floor(e.ideal - (OUT_OFFSET if e.kind == "out" else 0) + Fr(1, 2))


def is_out(s):
    return isinstance(s, (A.Drive, A.Release)) or (isinstance(s, A.Shift) and s.out)


def wires_of(s):
    return tuple(w.text for w in s.wires) if isinstance(s, A.Shift) else (s.wire.text,)


def paced_stmt(s):
    t = s.timing
    return t.for_ is not None or t.tol is not None or (isinstance(s, A.Shift) and s.each is not None)


class Timeline:
    """The chains of one seq body; chain_of maps id(statement) to its chain."""

    def __init__(self, sym, fclk):
        self.sym, self.f = sym, fclk
        self.chains, self.chain_of = [], {}
        self.cur, self.cursor, self.prev_gap = None, Fr(0), None

    def gap(self, s):
        t = s.each if isinstance(s, A.Shift) and s.each is not None else s.timing.for_
        return self.sym.eval(t).v * self.f if t is not None else Fr(0)

    def tol(self, s, gap):
        t = s.timing.tol
        if t is None:
            return Fr(0)
        q = self.sym.eval(t)
        return q.v * self.f if q.dim == 1 else q.v * gap

    def end(self):
        self.cur = None

    def walk(self, body):
        for s in body:
            self.stmt(s)

    def stmt(self, s):
        if isinstance(s, A.TIMED):
            if s.timing.window is not None:
                self.end()
            if self.cur is None:
                self.cur, self.cursor, self.prev_gap = Chain(), Fr(0), None
                self.chains.append(self.cur)
            reps = 1
            if isinstance(s, A.Shift):
                reps = int(self.sym.eval(s.count).v) // len(s.wires)
            g = self.gap(s)
            for _ in range(reps):
                before = g if self.prev_gap is None else self.prev_gap
                self.cur.entries.append(Entry([s], self.cursor, self.tol(s, before),
                                              "out" if is_out(s) else "in", wires_of(s)))
                self.cursor += g
                self.prev_gap = g
            self.cur.paced |= paced_stmt(s)
            self.chain_of[id(s)] = self.cur
        elif isinstance(s, A.Repeat):
            for _ in range(int(self.sym.eval(s.count).v)):
                self.walk(s.body)
        elif isinstance(s, A.If):
            self.branches(s)
        elif isinstance(s, (A.HostIO,)) and not s.pull:
            pass
        elif isinstance(s, (A.Emit, A.Set)):
            pass
        else:                           # await, wait, pull: the environment or a bare delay
            self.end()

    def branches(self, s):
        if self.cur is None:
            self.walk(s.then)
            self.end()
            self.walk(s.orelse or [])
            self.end()
            return
        chain, cursor, prev = self.cur, self.cursor, self.prev_gap
        n0 = len(chain.entries)
        self.walk(s.then)
        then = chain.entries[n0:]
        after = (self.cur, self.cursor, self.prev_gap)
        del chain.entries[n0:]
        self.cur, self.cursor, self.prev_gap = chain, cursor, prev
        self.walk(s.orelse or [])
        other = chain.entries[n0:]
        if self.cur is not chain or after[0] is not chain or len(then) != len(other) or \
                any(a.ideal != b.ideal or a.kind != b.kind for a, b in zip(then, other)):
            raise unsupported("an 'if' whose branches have different timed shapes inside a paced "
                              "sequence", s.span)
        for a, b in zip(then, other):
            a.stmts += b.stmts
            a.tol = min(a.tol, b.tol)
            a.wires = tuple(sorted(set(a.wires) | set(b.wires)))
        chain.entries[n0:] = then


def timeline(sym, body, fclk):
    t = Timeline(sym, fclk)
    t.walk(body)
    for c in t.chains:      # no `~`: the tolerance of the action it shares an instant with, or
        own = [e.stmts[0].timing.tol is not None for e in c.entries]    # of the one before it
        for k in range(len(c.entries) - 2, -1, -1):
            e, n = c.entries[k], c.entries[k + 1]
            if not own[k] and e.ideal == n.ideal and (own[k + 1] or n.tol):
                e.tol = n.tol
        for k in range(1, len(c.entries)):
            e = c.entries[k]
            if not own[k] and not e.tol:
                e.tol = c.entries[k - 1].tol
    keep = {id(c) for c in t.chains if c.paced}
    return {k: c for k, c in t.chain_of.items() if id(c) in keep}


def merged_drive(s, nxt, side):
    """s happens at the same instant as nxt and can ride on nxt's instruction as side-set."""
    return (isinstance(s, (A.Drive, A.Release)) and s.wire.text in side
            and s.timing.for_ is None and s.timing.window is None and s.timing.tol is None
            and isinstance(nxt, A.TIMED) and nxt.timing.window is None and is_out(nxt)
            and s.wire.text not in wires_of(nxt))


def side_wires(role):
    """Wires whose drives must share an instruction with the next action: a drive without
    `for` followed by a timed statement on another wire."""
    out = []

    def body(stmts):
        for k, s in enumerate(stmts):
            nxt = stmts[k + 1] if k + 1 < len(stmts) else None
            if isinstance(s, (A.Drive, A.Release)) and nxt is not None and \
                    s.timing.for_ is None and s.timing.window is None and \
                    isinstance(nxt, A.TIMED) and nxt.timing.window is None and \
                    s.wire.text not in wires_of(nxt) and s.wire.text not in out and \
                    (paced_stmt(nxt) or paced_stmt(s)):
                out.append(s.wire.text)
            for sub in sub_bodies(s):
                body(sub)
    for st in role.states:
        for e in st.edges:
            if isinstance(e, A.SeqEdge):
                body(e.body)
    return out


def sub_bodies(s):
    if isinstance(s, A.Repeat):
        return [s.body]
    if isinstance(s, A.If):
        return [s.then, s.orelse or []]
    return []


def apply_side(prog, labels, side, vout, levels0, span):
    """Every instruction carries both side-set bits, so each gets the side-set wires' levels
    after it (a forward dataflow over the laid-out program); two paths that disagree are an
    error. side: wires in side-set order (ss bit 0 first)."""
    if not side:
        return
    n = len(prog)
    lin = [None] * n
    lin[0] = dict(levels0)
    work = [0]
    while work:
        pc = work.pop()
        ins = prog[pc]
        out = dict(lin[pc])
        out.update(getattr(ins, "side", None) or {})
        ins.a["ss"] = sum(out.get(w, 0) << k for k, w in enumerate(side))
        succ = [] if ins.op == "JMP" and ins.a["cond"] == "ALWAYS" else [pc + 1]
        if ins.op == "JMP":
            succ.append(labels[ins.a["target"]])
        for q in succ:
            if q >= n:
                continue
            if lin[q] is None:
                lin[q] = out
                work.append(q)
            else:
                mine = getattr(prog[q], "side", None) or {}
                bad = next((w for w in side if w not in mine and lin[q].get(w) != out.get(w)), None)
                if bad is None:
                    continue
                raise TseError("R300", f"not supported by this version of tsec: side-set wire '{bad}' "
                               f"has different levels on two paths into instruction {q}", span)
