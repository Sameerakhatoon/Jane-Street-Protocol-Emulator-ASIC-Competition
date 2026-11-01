"""Stage 5c, timing of state machines: an explicit-state search over every timing.

The state of the search is the lane (pc, slot phase, the two buffer tags,
loop counters, the flag JMP TOUT tests) plus what the environment may still
do, kept as cycle offsets from now: the earliest cycle the last event could
have become visible (S8 gives the earliest next one from it), the visibility
interval of the occurrence windows are measured from, and the end of the
last level-establishing wait. Cycles come only from cycles.step().

At an edge wait for E, with e the earliest cycle E can be visible:
  - E is already there (e <= now): the wait ends at once; E was visible
    somewhere in [e, now], so actions anchored on E are up to now - e
    cycles later than the program alone says. If the opposite edge can
    also be there by now (now >= e + floor(min separation)), the edge can be
    missed altogether: a re-arm violation (R314).
  - E comes later: the wait ends at max(now + 1, e) + j for each slot phase j.
A pre-wait (the level an edge needs first) ends at once or later; a pull
from the host ends at any phase and forgets the environment; an alternate
(abort) can end any wait that carries it, and from then on every wait that
carries a level alternate ends at once until the abort target is reached.

States are merged on their normalised form, so loops terminate; offsets
older than a horizon are clamped: the longest separation the rules give plus
the longest window or distance, past which every older value acts the same.
"""
import math
from dataclasses import dataclass, field
from fractions import Fraction as Fr

from . import ast as A
from . import cycles, isa, printer
from .diag import TseError
from .semantics import min_separation, opposite, stable_after

DATA = "data"                  # a register value that depends on data (a count from a variable)
MAX_STATES = 1_500_000         # beyond this the analysis gives up (R325) instead of exhausting memory
DATA_FRAME = isa.DATA_SLOT_PERIOD   # the slot phase is kept modulo the data frame (LDW, STW)


@dataclass
class ObStat:
    ob: object
    ins: object
    dmin: int = None
    dmax: int = None
    where_min: str = ""
    where_max: str = ""
    unanchored: str = None


@dataclass
class Miss:
    ins: object
    lateness: int
    limit: int
    event: object
    prev: object
    rules: list
    where: str
    alt: bool = False


@dataclass
class Read:
    ins: object
    wire: str
    event: object
    hi: int                     # latest read, cycles after the occurrence became visible


@dataclass
class Result:
    obs: dict = field(default_factory=dict)         # id(ins) -> ObStat
    seps: dict = field(default_factory=dict)        # id(ins) -> (ins, least distance, where)
    misses: dict = field(default_factory=dict)      # id(ins) -> worst Miss
    reads: dict = field(default_factory=dict)       # id(ins) -> worst Read
    abort_latency: int = None
    states: int = 0
    paces: dict = field(default_factory=dict)       # id(chain) -> {entry: [dmin, dmax, ins, where]}
    pace_bad: dict = field(default_factory=dict)    # id(ins) -> (ins, why)


class Analysis:
    def __init__(self, sym, role, prog, labels, lane, fm, fclk, abort_targets=()):
        self.sym, self.role, self.prog, self.labels = sym, role, prog, labels
        self.lane, self.fm, self.f = lane, fm, fclk
        self.ms_cache, self.events = {}, {}
        self.abort_pcs = {labels[t] for t in abort_targets if t in labels}
        self.abort_event = self.abort_events()
        self.refs = {id(ins.sep.ref) for ins in prog if ins.sep is not None}
        self.mark_far = {}          # per reference: distances beyond this all act the same
        for ins in prog:
            if ins.sep is not None:
                far = math.ceil(max(ins.sep.ticks, ins.sep.hi or 0)) + 2 * DATA_FRAME
                k = id(ins.sep.ref)
                self.mark_far[k] = max(self.mark_far.get(k, 0), far)
        self.res = Result()
        self.clamp = self.horizon()
        self.liveness()

    def abort_events(self):
        """{abort pc: event} for the JMP TOUT targets that only an exact abort reaches (every
        alternate is the event, no pin check needed). Reaching one means the event happened
        when the alternate ended the wait, so later events count from it, not an older one."""
        evs = {}
        for prev, ins in zip(self.prog, self.prog[1:]):
            ab = prev.wait.abort if prev.wait is not None else None
            if ins.op != "JMP" or ins.a["cond"] != "TOUT" or ins.a["target"] not in self.labels:
                continue
            pc = self.labels[ins.a["target"]]
            exact = ab is not None and ab.mode != "host" and ab.check_level is None
            evs.setdefault(pc, set()).add(self.ev(ab.event) if exact else None)
        return {pc: next(iter(s)) for pc, s in evs.items()
                if len(s) == 1 and None not in s and pc in self.abort_pcs}

    def horizon(self):
        evs = set()
        for ins in self.prog:
            w = ins.wait
            if w is not None and w.event is not None:
                evs.add(self.ev(w.event))
                evs.add(self.ev(opposite(self.base(w.event))))
                if w.abort is not None:
                    evs.add(self.ev(w.abort.event))
        far = max([self.ms(a, b)[0] for a in evs for b in evs] or [0])
        span = [ins.ob.hi for ins in self.prog if ins.ob is not None]
        span += [ins.sep.ticks for ins in self.prog if ins.sep is not None]
        return int(far + max(span or [0])) + 2 * DATA_FRAME

    def successors(self, pc):
        ins = self.prog[pc]
        out = [] if ins.op == "JMP" and ins.a["cond"] == "ALWAYS" else [pc + 1]
        if ins.op == "JMP":
            out.append(self.labels[ins.a["target"]])
        return [p for p in out if p < len(self.prog)]

    def live(self, use, kill):
        """Per pc: can a use be reached before a kill (backward reachability)."""
        n = len(self.prog)
        live = [False] * n
        changed = True
        while changed:
            changed = False
            for pc in range(n - 1, -1, -1):
                ins = self.prog[pc]
                v = use(ins) or (not kill(ins) and any(live[s] for s in self.successors(pc)))
                if v and not live[pc]:
                    live[pc] = changed = True
        return live

    def liveness(self):
        """What each part of the state is still needed for, so dead values do not split states."""
        def edge(i):
            return i.wait is not None and i.wait.kind == "edge"

        def waits(i):
            return i.wait is not None and i.wait.kind in ("edge", "pre")

        def pull(i):
            return i.wait is not None and i.wait.kind == "pull"
        self.need_anchor = self.live(lambda i: i.ob is not None or i.reads is not None,
                                     lambda i: edge(i) or pull(i))
        self.need_pre = self.live(edge, lambda i: edge(i) or pull(i))
        self.need_last = self.live(waits, pull)
        self.need_mark = {}
        for ins in self.prog:
            if ins.sep is not None:
                ref = ins.sep.ref
                self.need_mark[id(ref)] = self.live(lambda i, s=ins: i is s, lambda i, r=ref: i is r)

        def reads_reg(r):
            conds = {"X": ("XDEC", "XZ", "XNEY"), "Y": ("YDEC", "XNEY")}[r]
            return lambda i: i.op == "JMP" and i.a["cond"] in conds

        def sets_reg(r):
            return lambda i: i.op in ("SET", "OUT", "MOV") and i.a.get("dst") == r
        self.need_chain = self.live(lambda i: bool(getattr(i, "paces", None)),
                                    lambda i: waits(i) or pull(i))
        self.need_x = self.live(reads_reg("X"), sets_reg("X"))
        self.need_y = self.live(reads_reg("Y"), sets_reg("Y"))
        self.phase_mod = DATA_FRAME if any(i.op in ("LDW", "STW") for i in self.prog) else self.fm.S

    # ------------------------------------------------------------ environment
    def ev(self, e):
        k = printer.event(e)
        self.events.setdefault(k, e)
        return k

    def ms(self, a, b):
        key = (a, b)
        if key not in self.ms_cache:
            t, used = min_separation(self.sym, self.role, self.events[a], self.events[b], self.f)
            self.ms_cache[key] = (math.floor(t), used)
        return self.ms_cache[key]

    # ------------------------------------------------------------ search
    def run(self):
        t0 = cycles.start_cycle(self.lane, self.fm)
        start = (0, t0, (0,), None, None, 0, None, None, None, (), None, None)
        todo, seen = [self.norm(start)], set()
        while todo:
            st = todo.pop()
            if st in seen:
                continue
            seen.add(st)
            if len(seen) > MAX_STATES:
                raise TseError("R325", f"timing analysis of role '{self.role.name.text}' exceeds "
                               f"{MAX_STATES} states; split long waits or shorten the role",
                               self.role.name.span)
            for nxt in self.step(st):
                n = self.norm(nxt)
                if n not in seen:
                    todo.append(n)
        self.res.states = len(seen)
        return self.res

    def norm(self, s):
        pc, t, buf, x, y, tf, last, anchor, pre, marks, abort, chain = s
        if pc >= len(self.prog):
            return s
        m = self.phase_mod
        base = t - t % m

        def off(v):
            return None if v is None else max(v - base, t % m - self.clamp)
        last = None if last is None or not self.need_last[pc] else (last[0], off(last[1]))
        anchor = None if anchor is None or not self.need_anchor[pc] else \
            (anchor[0], off(anchor[1]), off(anchor[2]))
        pre = None if pre is None or not self.need_pre[pc] else (pre[0], off(pre[1]))
        marks = tuple((k, max(off(v), t % m - self.mark_far[k])) for k, v in marks
                      if self.need_mark[k][pc])
        abort = off(abort)
        x = x if self.need_x[pc] else None
        y = y if self.need_y[pc] else None
        chain = None if chain is None or not self.need_chain[pc] else (chain[0], chain[1] - base, chain[2])
        return (pc, t - base, buf, x, y, tf, last, anchor, pre, marks, abort, chain)

    def step(self, s):
        pc, t, buf, x, y, tf, last, anchor, pre, marks, abort, chain = s
        if pc >= len(self.prog):
            return []
        ins = self.prog[pc]
        where = f"phase {t % self.fm.S}"
        if getattr(ins, "rewait", False) and abort is not None:
            self.rewait_check(ins, t, abort, where)
            abort = None            # the change was not the abort event: back to normal
        if pc in self.abort_pcs and abort is not None:
            lat = t - abort
            if self.res.abort_latency is None or lat > self.res.abort_latency:
                self.res.abort_latency = lat
            if pc in self.abort_event:  # the abort event is now the last one seen; it became
                # visible no earlier than the dispatch of the wait it ended (up to S - 1 before)
                last = (self.abort_event[pc], abort - (self.fm.S - 1))
            abort = None
        checking = abort is None        # after an abort only the latency to its state matters
        if ins.ob is not None and checking:
            self.record_ob(ins, t, anchor, where)
        if ins.sep is not None and checking:
            ref = dict(marks).get(id(ins.sep.ref))
            if ref is not None:
                d = t - ref
                old = self.res.seps.get(id(ins))
                lo = d if old is None or d < old[1] else old[1]
                hi = d if old is None or d > old[3] else old[3]
                self.res.seps[id(ins)] = (ins, lo, where if old is None or d < old[1] else old[2], hi)
        if id(ins) in self.refs:
            marks = tuple((k, v) for k, v in marks if k != id(ins)) + ((id(ins), t),)
        if ins.reads is not None and anchor is not None and checking \
                and not getattr(ins, "abort_check", False):
            self.record_read(ins, t, anchor)
        for pc_ in getattr(ins, "paces", None) or ():
            if checking:
                chain = self.record_pace(ins, pc_, t, chain, where)
        w = ins.wait
        if (w is not None and w.kind in ("edge", "pre", "pull")) or (ins.op == "PULL" and ins.a.get("blk")):
            chain = None                # waiting for the environment ends a paced chain
        out = []

        def go(npc, end=None, **kw):
            b = list(buf)
            nt = cycles.step(ins.op, ins.a, t, pc, npc, b, self.lane, self.fm, end)
            st = dict(x=x, y=y, tf=tf, last=last, anchor=anchor, pre=pre, marks=marks, abort=abort)
            st.update(kw)
            out.append((npc, nt, tuple(b), st["x"], st["y"], st["tf"], st["last"], st["anchor"],
                        st["pre"], st["marks"], st["abort"], chain))

        if ins.op == "WAIT" and w is not None and w.kind in ("edge", "pre"):
            self.wait(ins, w, t, last, anchor, pre, abort, go, where)
        elif ins.op == "WAIT" and w is not None and w.kind == "test":
            go(pc + 1, t, tf=0)
            go(pc + 1, t, tf=1)
        elif ins.op == "PULL" and ins.a.get("blk"):
            for j in range(self.fm.S):
                go(pc + 1, t + j, last=None, anchor=None, pre=None)
        elif ins.op == "JMP":
            c = ins.a["cond"]
            tgt = self.labels[ins.a["target"]]
            if c == "ALWAYS":
                go(tgt)
            elif c in ("XDEC", "YDEC"):
                v = x if c == "XDEC" else y
                if v is None:
                    raise ValueError(f"loop counter unknown at {pc}")
                if v == DATA:           # a count from data: any number of passes
                    go(tgt)
                    go(pc + 1)
                else:
                    nv = (v - 1) & 0xFFFF
                    kw = {"x": nv} if c == "XDEC" else {"y": nv}
                    go(tgt if v != 0 else pc + 1, **kw)
            elif c == "TOUT":
                if tf in (1, None):
                    go(tgt)
                if tf in (0, None):
                    go(pc + 1)
            else:                       # XNEY, PIN, XZ, GUARD: data, both ways
                go(tgt)
                go(pc + 1)
        else:
            nx, ny = x, y
            if ins.op == "SET" and ins.a["dst"] in ("X", "Y"):
                nx, ny = (ins.a["imm"], y) if ins.a["dst"] == "X" else (x, ins.a["imm"])
            elif ins.op in ("OUT", "MOV") and ins.a.get("dst") in ("X", "Y"):
                nx, ny = (DATA, y) if ins.a["dst"] == "X" else (x, DATA)
            elif ins.op == "SET" and ins.a["dst"] == "XH":
                nx = DATA
            elif ins.op == "CSRR":
                nx, ny = DATA, DATA
            go(pc + 1, x=nx, y=ny)
        return out

    def wait(self, ins, w, t, last, anchor, pre, abort, go, where):
        S = self.fm.S
        pc = self.prog.index(ins) if False else None
        npc = self.index(ins) + 1
        E = self.ev(w.event)
        if abort is not None and w.abort is not None and w.abort.mode == "level":
            go(npc, t, tf=1)            # the alternate level is still there
            return
        e = None
        if last is not None:
            e = last[1] + self.ms(last[0], E)[0]
        if pre is not None and pre[0] == w.wire:
            e = pre[1] + 1 if e is None else max(e, pre[1] + 1)
        if w.kind == "edge":
            own = getattr(w, "own", False)
            if e is None or e <= t:
                vis_lo = e if e is not None else (t if own else t - self.clamp)
                if abort is None and not own:   # only we pull the wire low again after our release
                    self.check_miss(ins, w, E, t, e, last, where)
                go(npc, t, tf=0, last=(E, e) if e is not None else None,
                   anchor=(E, vis_lo, t), pre=None)
                first = t + 1
            else:
                first = e
            for j in range(S):
                end = first + j
                go(npc, end, tf=0, last=(E, end), anchor=(E, end, end), pre=None)
        else:
            go(npc, t, tf=0, pre=(w.wire, t))
            P = E
            first = t + 1 if e is None else max(t + 1, e)
            for j in range(S):
                end = first + j
                go(npc, end, tf=0, last=(P, end), pre=(w.wire, end))
        if w.abort is not None:
            A_ = self.ev(w.abort.event)
            if w.abort.mode == "change" and last is not None and abort is None \
                    and not getattr(ins, "rewait", False):
                lim = last[1] + self.ms(last[0], A_)[0]
                if t >= lim:
                    self.record_miss(Miss(ins, t - lim, 0, w.abort.event, self.events[last[0]],
                                          self.ms(last[0], A_)[1], where, alt=True))
            host = w.abort.mode == "host"       # the host's word ends it: no abort, a branch
            for j in range(S):          # ended on the alternate: its own level was absent at end
                end = t + j
                go(npc, end, tf=1, abort=None if host else end,
                   pre=(w.wire, end) if w.kind == "edge" else pre,
                   anchor=anchor if host else (A_, end, end))

    def rewait_check(self, ins, t, change, where):
        """After a change that was not the abort event (a start where a stop aborts), the wait
        is armed again; the abort event may follow that change at its own separation."""
        ab = ins.wait.abort
        if ab is None or ab.check_level is None:
            return
        other = A.Event("fall" if ab.check_level else "rise", A.Ident(ab.check_wire))
        sep, used = min_separation(self.sym, self.role, other, ab.event, self.f,
                                   {ab.guard_wire: ab.guard_level})
        lim = change + math.floor(sep)
        if t >= lim:
            self.record_miss(Miss(ins, t - lim, 0, ab.event, other, used, where, alt=True))

    def index(self, ins):
        if not hasattr(self, "_index"):
            self._index = {id(i): k for k, i in enumerate(self.prog)}
        return self._index[id(ins)]

    def check_miss(self, ins, w, E, t, e, last, where):
        """The level after E must still be there when the wait looks at it."""
        if e is None:
            self.record_miss(Miss(ins, self.clamp, 0, w.event, None, [], where))
            return
        opp = self.ev(opposite(self.base(w.event)))
        limit, used = self.ms(E, opp)
        if used and t - e >= max(limit, 1):
            self.record_miss(Miss(ins, t - e, limit, w.event, self.events[last[0]] if last else None,
                                  used, where))

    def base(self, e):
        from .semantics import base_event
        return base_event(self.sym, e)[0]

    def record_miss(self, m):
        old = self.res.misses.get(id(m.ins))
        if old is None or m.lateness - m.limit > old.lateness - old.limit:
            self.res.misses[id(m.ins)] = m

    def record_ob(self, ins, t, anchor, where):
        ob = ins.ob
        stat = self.res.obs.setdefault(id(ins), ObStat(ob, ins))
        if anchor is None or anchor[0] != self.ev(ob.event):
            stat.unanchored = (f"on some path it runs without first seeing {printer.event(ob.event)} "
                               f"({where})")
            return
        lo, hi = t - anchor[2], t - anchor[1]
        if stat.dmin is None or lo < stat.dmin:
            stat.dmin, stat.where_min = lo, where
        if stat.dmax is None or hi > stat.dmax:
            stat.dmax, stat.where_max = hi, where

    def record_pace(self, ins, pc_, t, chain, where):
        """One timed action of a paced chain: its distance from the chain's first action, per
        entry of the timeline (the entry count since the first action says which one)."""
        ent = pc_.chain.entries
        if pc_.entry is not None and pc_.entry > 0 and chain is not None and chain[0] == id(pc_.chain):
            k = pc_.entry               # reordered unrolled code: the entry is known
            self.note_pace(chain, k, t, ins, where)
            return (chain[0], chain[1], max(chain[2], k + 1))
        if chain is None or chain[0] != id(pc_.chain) or chain[2] >= len(ent) or pc_.entry == 0:
            if not any(x is pc_.stmt for x in ent[0].stmts):
                self.res.pace_bad.setdefault(id(ins), (ins, f"on some path it runs without the first "
                                                       f"action of its sequence before it ({where})"))
                return chain
            chain = (id(pc_.chain), t, 0)
        k = chain[2]
        if not any(x is pc_.stmt for x in ent[k].stmts):
            self.res.pace_bad.setdefault(id(ins), (ins, f"it runs out of the order of its sequence "
                                                   f"({where})"))
            return None
        self.note_pace(chain, k, t, ins, where)
        return (chain[0], chain[1], k + 1)

    def note_pace(self, chain, k, t, ins, where):
        d = t - chain[1]
        rec = self.res.paces.setdefault(chain[0], {})
        r = rec.get(k)
        if r is None:
            rec[k] = [d, d, ins, where, where]
        else:
            if d < r[0]:
                r[0], r[3] = d, where
            if d > r[1]:
                r[1], r[4] = d, where

    def record_read(self, ins, t, anchor):
        hi = t - anchor[1] + 1
        old = self.res.reads.get(id(ins))
        if old is None or hi > old.hi:
            self.res.reads[id(ins)] = Read(ins, ins.reads, self.events[anchor[0]], hi)


def stability_warnings(sym, role, res, fclk, pads):
    """W260 data: (ins, wire, event, latest read in ticks, stable ticks) where a pin test may read a
    wire after the datasheet stops guaranteeing it."""
    out = []
    for r in res.reads.values():
        stable = stable_after(sym, role, r.event, r.wire, fclk)
        latest = r.hi + pads[1]
        if latest > stable:
            out.append((r.ins, r.wire, r.event, latest, stable))
    return out
