"""Stage 4, lowering: a frame's obligations to lane instructions.

Supported shape: the initial state has one
`on` edge (host.pull, fall or rise of a pin) into a state whose only edge is a
`seq` back to the initial state. The seq holds drive, shift and sample
statements, optionally a window on the first one, and push host at the end.
Anything else is rejected with R300 and the reason, never silently dropped
(rule R1).

Two pacing strategies: "dly" (delay loops, the default) and "timer" (the
lane's DPLL as a bit timer, for fractional periods). Delays marked tunable
are fixed later by the solver in schedule.py.
"""
from fractions import Fraction as Fr

from . import ast as A
from . import isa
from .diag import TseError
from .lir import Ins
from .semantics import frame as desugar


def unsupported(msg, span):
    return TseError("R300", f"not supported by this version of tsec: {msg}", span)


def frame_shape(role):
    """(anchor event, seq edge) of a role in the supported shape, else R300."""
    states = {s.name.text: s for s in role.states}
    init = next((s for s in role.states if s.initial), role.states[0])
    if len(init.edges) != 1 or not isinstance(init.edges[0], A.OnEdge):
        raise unsupported(f"state '{init.name.text}' must have exactly one 'on' edge", init.name.span)
    on = init.edges[0]
    if on.guard is not None or on.body is not None:
        span = on.guard.span if on.guard is not None else on.span
        raise unsupported("'when' and 'do' on the anchor edge", span)
    ev = on.event
    if not ((ev.kind in ("fall", "rise")) or (ev.name.text == "host" and ev.attr is not None
                                               and ev.attr.text == "pull")):
        raise unsupported("the anchor must be host.pull(v), fall(w) or rise(w)", ev.span)
    body_state = states[on.target.text]
    if len(body_state.edges) != 1 or not isinstance(body_state.edges[0], A.SeqEdge):
        raise unsupported(f"state '{body_state.name.text}' must have exactly one 'seq' edge",
                          body_state.name.span)
    seq = body_state.edges[0]
    if seq.target.text != init.name.text:
        raise unsupported("the seq must return to the initial state", seq.target.span)
    if seq.abort_on is not None:
        raise unsupported("'abort on'", seq.abort_on.span)
    if len(role.states) != 2:
        extra = next(s for s in role.states if s is not init and s is not body_state)
        raise unsupported("roles with more than two states", extra.name.span)
    timed_seen, pushes_seen = 0, False
    for s in seq.body:
        if isinstance(s, A.TIMED):
            if pushes_seen:
                raise unsupported("timed statements after 'push host'", s.span)
            if isinstance(s, A.Release) or len(getattr(s, "wires", [None])) > 1:
                raise unsupported("release and multi-pin shifts", s.span)
            if s.timing.window is not None and timed_seen:
                raise unsupported("a window on any statement but the first", s.timing.window.span)
            if isinstance(s, A.Shift) and s.each is None:
                raise unsupported("a shift without 'each'", s.span)
            if isinstance(s, A.Sample) and s.expect is None:
                raise unsupported("a sample without 'expect'", s.span)
            timed_seen += 1
        elif isinstance(s, A.HostIO) and not s.pull:
            pushes_seen = True
        else:
            raise unsupported(f"'{type(s).__name__.lower()}' inside seq", s.span)
    if not timed_seen:
        raise unsupported("a seq without timed statements", seq.span)
    return ev, seq


class Lowering:
    def __init__(self, sym, role, fclk, mode):
        self.sym, self.role, self.f, self.mode = sym, role, fclk, mode
        self.prog, self.nco_period, self.timer_q16, self.nco_phase = [], None, None, Fr(0)
        if len(role.uses) > isa.VPINS:
            raise TseError("R307", f"role '{role.name.text}' uses {len(role.uses)} wires; a lane "
                           f"has {isa.VPINS} pins", role.uses[isa.VPINS].span)
        anchor, seq = frame_shape(role)
        self.frame = desugar(sym, role, anchor, seq.body, fclk)

    def emit(self, op, **a):
        ins = Ins(op, a)
        self.prog.append(ins)
        return ins

    def vpin(self, wire):
        return [u.text for u in self.role.uses].index(wire)

    def delay(self, n):
        """n extra cycles: one tunable DLY, preceded by fixed maximal DLYs when n does not fit."""
        n = max(0, n)
        while n > isa.DLY_MAX:
            self.emit("DLY", d=isa.DLY_MAX).note = "long delay, first part (9-bit DLY field)"
            n -= isa.DLY_MAX + 1
        ins = self.emit("DLY", d=n)
        ins.tune = True
        return ins

    def align_even(self):
        if len(self.prog) % 2:
            self.emit("DLY", d=0).note = "pad: start the next pair on an SRAM word"

    def phase_lock(self, xload=None):
        """A 1-cycle instruction ending a word, so the next word is a miss that dispatches at
        s + lat for the lane's slot s: from there the timing no longer depends on the phase
        the anchor had (re-alignment after a wait)."""
        if len(self.prog) % 2 == 0:
            self.emit("DLY", d=0).note = "pad"
        if xload is None:
            self.emit("DLY", d=0).note = "phase lock: next word fetched in this lane's slot"
        else:
            self.emit("SET", dst="X", imm=xload).note = "loop count, also the phase lock"

    def lower(self):
        self.prologue()
        ev = self.frame.anchor
        if ev.kind == "named":
            a = self.emit("PULL", blk=1)
        else:
            a = self.emit("WAIT", pol=0 if ev.kind == "fall" else 1, src="PIN",
                          idx=self.vpin(ev.name.text))
        a.label, a.note = "idle", "anchor"
        self.lower_actions()
        self.emit("JMP", cond="ALWAYS", target="idle")
        return self.prog

    def prologue(self):
        """Output wires at their idle level, then their pads turned on: once, before the anchor."""
        mask = val = 0
        for k, u in enumerate(self.role.uses):
            w = self.sym.wires[u.text]
            if w.dir == "in":
                continue
            idle = w.attr("idle")
            lvl = 1 if w.open_drain else (int(self.sym.eval(idle.value).v) if idle else 0)
            mask, val = mask | 1 << k, val | lvl << k
        if mask:
            self.emit("SET", dst="PINS", imm=mask << 4 | val).note = "idle levels before driving"
            self.emit("SET", dst="PINDIRS", imm=mask << 4 | mask).note = "outputs on"

    def lower_actions(self):
        acts = self.frame.actions
        nco = self.mode == "timer"
        if nco:
            self.timer_q16 = round(acts[0].gap * 16)       # DPLL period in 1/16 cycle
            self.nco_period = Fr(self.timer_q16, 16)
        loops = [ob.reps - 1 for ob in acts if ob.reps > 1]
        hoisted = not nco and len(loops) == 1     # the loop count doubles as the phase lock
        xload = loops[0] if hoisted else None
        for k, ob in enumerate(acts):
            first, reps = k == 0, ob.reps
            if first and ob.window:
                self.delay(int(ob.ideals[0]) - 2)
            if reps > 1 and not hoisted:
                self.emit("SET", dst="X", imm=reps - 1)
            if nco and first:                     # DPLL start shares a word with the action
                self.align_even()
                self.emit("SET", dst="CMD", imm=isa.CMD_DPLL_ON).note = "start bit timer in phase"
            elif nco:                              # tick wait shares a word with the action
                self.align_even()
                w = self.emit("WAIT", pol=1, src="EV", idx=isa.EV_BIT_START)
                w.note = "bit timer tick"
                if reps > 1:
                    w.label = head = f"L{len(self.prog) - 1}"
            elif reps > 1 and len(self.prog) % 2:  # the loop starts a word: fits the buffer
                pad = self.emit("DLY", d=3)        # 4 cycles: the head's fetch never stalls
                pad.tune, pad.note = True, "pad: loop starts a word (4 cycles, no fetch stall)"
            if first and not nco:
                self.phase_lock(xload)
            body = self.action(ob.stmt)
            body.ob = ob
            if reps > 1 and not (nco and not first):
                body.label = head = f"L{len(self.prog) - 1}"
            last_in = k == len(acts) - 1 and ob.kind == "in"
            if not nco and not last_in:            # inputs re-arm at once; the timer paces itself
                self.delay(int(ob.gap) - 3)
            if reps > 1:
                self.emit("JMP", cond="XDEC", target=head)
        if nco:
            if acts[-1].kind == "out":
                self.align_even()
                self.emit("WAIT", pol=1, src="EV", idx=isa.EV_BIT_START).note = "hold for the last gap"
            self.emit("SET", dst="CMD", imm=isa.CMD_DPLL_OFF)
        for _ in self.frame.pushes:
            self.emit("PUSH", blk=1)

    def action(self, s):
        m = 1 << self.vpin(s.wire.text)
        if isinstance(s, A.Drive):
            level = self.sym.eval(s.value).v
            return self.emit("SET", dst="PINS", imm=m << 4 | (m if level else 0))
        if isinstance(s, A.Shift):
            if s.out:
                return self.emit("OUT", dst="PINS", n=1)
            return self.emit("IN", src="PINS", n=1)
        if self.sym.eval(s.expect).v == 0:
            # expect 0: abort the frame if the pin is high
            return self.emit("JMP", cond="PIN", target="idle", expect_taken=False)
        nxt = f"ok{len(self.prog)}"              # expect 1: skip the error flag if the pin is high
        j = self.emit("JMP", cond="PIN", target=nxt, expect_taken=True)
        self.emit("SET", dst="IRQ", imm=1).note = "framing error"
        return j
