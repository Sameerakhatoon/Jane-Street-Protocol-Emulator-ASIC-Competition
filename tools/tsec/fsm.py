"""Stage 4b, state-machine lowering: any role's states and edges to lane IR.

The paced-frame lowering (lower.py) handles one shape exactly and finds
pacing loops for it; this lowering handles the event-driven rest: `respond`,
`on ... when ... do`, `seq ... abort on`, `await`, `repeat`, `if`, `wait`,
windows after pin events, host mailboxes and `emit`.

How each construct becomes lane code:
  rise(w) / fall(w)   WAIT for the opposite level first (unless the last wait
                      on w already saw it), then WAIT for the level: WAIT is
                      level-sensitive, so the pair is an edge
  named event         its pin edge, then its guard; a failed guard waits again
  host.pull(v)        PULL block; afterwards nothing is known about the wires
  window after E      wait for the next E (or use the occurrence an edge or
                      await just saw), a tunable DLY, then the action
  guard on a var      MOV OSR, ISR; OUT NULL, lo; OUT X, width; SET Y, c; JMP XNEY
  guard on a wire     JMP PIN on LANE_CFG.jmp_pin, otherwise WAIT v, PIN, p, to
                      with T = 0 (ends in one cycle) and JMP TOUT
  repeat N            a counted loop on Y (rolled) or N copies (unrolled)
  abort on rise(w)    LANE_CFG alternate = level of w; every wait in the seq
                      carries alt; JMP TOUT after the waiting statements
  abort on E when g   alternate = "w changed since dispatch" on the waits
                      during which g holds; JMP TOUT after each, then a pin
                      check tells E from the other changes
  wait T              a tunable DLY with a minimum-distance obligation

Shift pins: OUT and IN use virtual pin 0, so the wire shifted in is pin 0's
input and the wire shifted out is pin 0's output (the crossbar routes them to
different pads). Other wires get pins 1 to 3.
"""
import math
from dataclasses import dataclass, field
from fractions import Fraction as Fr

from . import ast as A
from . import isa, pace, printer
from .diag import TseError
from .lir import Abort, Ins, Pace, Sep, Wait, WinOb
from .lower import unsupported
from .semantics import base_event

STRATEGIES = ("rolled", "unrolled")


@dataclass
class Facts:
    """What the code knows at a point: last level seen per wire, and which var is in ISR/OSR."""
    levels: dict = field(default_factory=dict)
    isr: str = None
    osr: str = None

    def copy(self):
        return Facts(dict(self.levels), self.isr, self.osr)

    def meet(self, o):
        return Facts({w: v for w, v in self.levels.items() if o.levels.get(w) == v},
                     self.isr if self.isr == o.isr else None, meet_osr(self.osr, o.osr))

    def key(self):
        return (tuple(sorted(self.levels.items())), self.isr, self.osr)


@dataclass
class Ctx:
    anchor: object = None       # the event occurrence the last wait (or edge) saw
    fresh: bool = False         # a seq statement has not used that occurrence yet
    block: bool = False         # inside an edge's do or respond block: the occurrence is shared
    respond: object = None      # RespondEdge whose window covers the block
    abort: object = None        # Abort of the enclosing seq
    fail: str = None            # where a failed `expect` goes


class Fsm:
    def __init__(self, sym, role, fclk, strategy="rolled"):
        self.sym, self.role, self.f, self.strategy = sym, role, fclk, strategy
        self.states = {s.name.text: s for s in role.states}
        self.init = next((s for s in role.states if s.initial), role.states[0])
        self.prog, self.nlabel = [], 0
        self.alt, self.jmp_pin, self.need_t0 = None, None, False
        self.counters = []                    # registers held by enclosing rolled loops
        self.host_notes, self.emit_codes = {}, {}
        self.exits = []
        self.last_effect, self.pending_sep, self.last_drive = None, None, {}
        self.wires = {w.text: sym.wires[w.text] for w in role.uses}
        self.side = pace.side_wires(role)     # side-set wires, ss bit 0 first
        self.chains, self.chain_list, self.pending_side, self.pending_paces = {}, [], {}, []
        self.next_stmt, self.entry, self.pace_hint = None, None, None
        self.allocate_pins()

    # ------------------------------------------------------------ pins
    def allocate_pins(self):
        """Shifted-out wires on virtual pins 0 up (OUT fills PINS from bit 0), the shifted-in
        wire on pin 0's input, side-set wires on pins 2 and 3, the rest on the free pins; a pin
        under side-set is driven by every instruction, so only an input may share it."""
        shin, shout, driven, read = set(), set(), set(), set()

        def walk(x):
            if isinstance(x, A.Shift):
                if x.out:
                    shout.add(tuple(w.text for w in x.wires))
                else:
                    shin.update(w.text for w in x.wires)
            elif isinstance(x, (A.Drive, A.Release)):
                driven.add(x.wire.text)
            elif isinstance(x, A.Event) and x.kind != "named":
                read.add(x.name.text)
            if isinstance(x, list):
                for y in x:
                    walk(y)
            elif hasattr(x, "__dataclass_fields__"):
                for k in x.__dataclass_fields__:
                    if k != "span":
                        walk(getattr(x, k))
        walk(self.role.states)
        for ev in self.sym.events.values():
            walk(ev)
        span = self.role.name.span
        if len(shin) > 1:
            raise TseError("R307", f"pin plan impossible: {' and '.join(sorted(shin))} are all "
                           "shifted in, and shifts use virtual pin 0 only", span)
        if len(shout) > 1:
            names = sorted({w for g in shout for w in g})
            raise TseError("R307", f"pin plan impossible: {' and '.join(names)} are all shifted out "
                           "by different statements, and OUT fills the pins from virtual pin 0", span)
        self.pin_in, self.pin_out = {}, {}
        win, wout = next(iter(shin), None), list(next(iter(shout), ()))
        if win and wout and win != wout[0] and (wout[0] in read or win in driven):
            raise TseError("R307", f"pin plan impossible: '{win}' (shifted in) and '{wout[0]}' (shifted "
                           "out) share virtual pin 0, so neither can also be used the other way", span)
        for k, w in enumerate(wout):
            self.pin_out[w] = k
            if w in read and (k > 0 or win in (None, w)):
                self.pin_in[w] = k
        if win:
            self.pin_in[win] = 0
            if win in driven and (not wout or wout[0] == win):
                self.pin_out[win] = 0
        used = set(range(len(wout))) | ({0} if win else set())
        under_side = set()
        if self.side:
            clash = [w for w in self.side if w in wout or w == win]
            if len(self.side) > 2 or clash or len(wout) > 2:
                raise TseError("R307", "pin plan impossible: side-set needs virtual pins 2 and 3 for "
                               f"{' and '.join(self.side)}", span)
            for k, w in enumerate(self.side):
                self.pin_in[w] = self.pin_out[w] = 2 + k
            used |= {2, 3}
            under_side = {3} if len(self.side) == 1 else set()
        for u in self.role.uses:
            w = u.text
            if w in wout or w == win or w in self.side:
                continue
            free = [k for k in range(1, isa.VPINS) if k not in used]
            if not free and self.wires[w].dir == "in" and under_side:
                free = sorted(under_side)
                under_side = set()
            if not free:
                raise TseError("R307", f"role '{self.role.name.text}' needs more than {isa.VPINS} "
                               "virtual pins", u.span)
            self.pin_in[w] = self.pin_out[w] = free[0]
            used.add(free[0])

    def side_levels(self):
        """Idle levels of the side-set wires, the side-set state at RUN."""
        out = {}
        for w in self.side:
            d = self.wires[w]
            idle = d.attr("idle")
            out[w] = 1 if d.open_drain else (int(self.sym.eval(idle.value).v) if idle else 0)
        return out

    def vin(self, w):
        return self.pin_in[w]

    def vout(self, w):
        return self.pin_out[w]

    # ------------------------------------------------------------ emission
    def label(self, stem="L"):
        self.nlabel += 1
        return f"{stem}{self.nlabel}"

    def emit(self, op, **a):
        ins = Ins(op, a)
        self.prog.append(ins)
        return ins

    def place(self, name):
        """Put label name on the next instruction (a no-op marker resolved at layout)."""
        self.prog.append(Ins("LABEL", {}, label=name))

    def jump(self, target, cond="ALWAYS"):
        return self.emit("JMP", cond=cond, target=target)

    def goto(self, state, facts, why="edge"):
        self.exits.append((state, facts.copy()))
        self.jump(state).note = f"-> {state}"

    # ------------------------------------------------------------ whole role
    def lower(self):
        entry = {s.name.text: None for s in self.role.states}
        entry[self.init.name.text] = Facts()
        for _ in range(12):
            self.reset()
            for st in self.role.states:
                if entry[st.name.text] is not None:
                    self.state(st, entry[st.name.text].copy())
            new = dict(entry)
            new[self.init.name.text] = Facts()
            for target, facts in self.exits:
                new[target] = facts if new[target] is None else new[target].meet(facts)
            if all(same_facts(new[k], entry[k]) for k in entry):
                break
            entry = new
        self.reset()
        for st in self.role.states:
            self.state(st, (entry[st.name.text] or Facts()).copy())
        body = self.prog
        self.prog = []
        self.prologue()
        self.prog += body
        return self.prog

    def reset(self):
        self.prog, self.exits, self.nlabel = [], [], 0
        self.chain_list, self.pending_side, self.pending_paces = [], {}, []
        self.last_effect, self.pending_sep, self.last_drive = None, None, {}

    def prologue(self):
        outs = [w for w, d in self.wires.items() if d.dir in ("out", "inout") and w in self.pin_out]
        if outs:
            mask = val = 0
            for w in outs:
                d = self.wires[w]
                idle = d.attr("idle")
                lvl = 1 if d.open_drain else (int(self.sym.eval(idle.value).v) if idle else 0)
                mask |= 1 << self.vout(w)
                val |= lvl << self.vout(w)
            self.emit("SET", dst="PINS", imm=mask << 4 | val).note = "idle levels before driving"
            on = sum(1 << self.vout(w) for w in outs
                     if self.wires[w].dir == "out" or self.wires[w].open_drain)
            self.emit("SET", dst="PINDIRS", imm=mask << 4 | on).note = "outputs on (turnaround pins off)"
        if self.need_t0:
            self.emit("SET", dst="T", imm=0).note = "T = 0: a WAIT with timeout tests a pin"
            self.emit("SET", dst="T", imm=0)
        self.jump(self.init.name.text)

    def state(self, st, facts):
        self.place(st.name.text)
        self.last_effect = None         # a wait measures from our last output on this path only
        edges = st.edges
        if not edges:
            raise unsupported(f"state '{st.name.text}' has no edges", st.name.span)
        if all(isinstance(e, (A.OnEdge, A.RespondEdge)) for e in edges):
            self.event_edges(st, edges, facts)
        elif len(edges) == 1 and isinstance(edges[0], A.SeqEdge):
            self.seq(st, edges[0], facts)
        elif all(isinstance(e, A.AfterEdge) for e in edges):
            self.after_edges(st, edges, facts)
        else:
            e = next(e for e in edges if not isinstance(e, (A.OnEdge, A.RespondEdge)))
            raise unsupported("a state must have one seq edge, or on and respond edges on one event "
                              f"(state '{st.name.text}')", e.span)

    # ------------------------------------------------------------ waiting
    def wait_for(self, ev, facts, ctx, force_pre=False):
        """Lower waiting for one occurrence of ev; returns the label to wait again. A `wait`
        after it counts from its end, not from an output before it."""
        self.last_effect = None
        base, guard = base_event(self.sym, ev)
        if ev.kind == "named" and ev.name.text == "host":
            if ev.attr.text != "pull":
                raise unsupported(f"waiting for host.{ev.attr.text}", ev.span)
            again = self.label()
            self.place(again)
            w = self.emit("PULL", blk=1)
            w.wait, w.note = Wait("pull"), "wait for the host"
            facts.levels.clear()
            if ev.args and isinstance(ev.args[0], A.Ref):
                self.bind_osr(ev.args[0].name.text, facts)
            return again
        if base.kind == "named" or base.kind == "taken":
            raise unsupported(f"waiting for {printer.event(ev)}", ev.span)
        wire = base.name.text
        again = self.label()
        self.place(again)
        if base.kind == "edge":
            self.wait_ins(1, 4 + self.vin(wire), Wait("edge", ev, wire, None), ctx)
            facts.levels.pop(wire, None)
        else:
            lvl = 1 if base.kind == "rise" else 0
            if force_pre or guard is not None or facts.levels.get(wire) != 1 - lvl:
                self.wait_ins(1 - lvl, self.vin(wire), Wait("pre", A.Event(
                    "fall" if lvl else "rise", base.name), wire, 1 - lvl), ctx)
            self.wait_ins(lvl, self.vin(wire), Wait("edge", ev, wire, lvl), ctx)
            facts.levels[wire] = lvl
        if guard is not None:
            self.test(guard, again, facts, after=ev)
        return again

    def wait_ins(self, pol, idx, wait, ctx):
        ins = self.emit("WAIT", pol=pol, src="PIN", idx=idx)
        ins.wait = wait
        ab = ctx.abort if ctx is not None else None
        if ab is not None and self.carries(ab, wait):
            ins.a["alt"] = 1
            wait.abort = ab
            if ab.mode == "host":
                self.jump(ab.target, "TOUT").note = "alternate: the host sent a word"
            elif ab.mode == "change":
                chk = self.label("abortchk")
                self.jump(chk, "TOUT").note = "alternate: maybe the abort event"
                self.deferred.append((chk, ab, ins))
        return ins

    def carries(self, ab, wait):
        if ab.mode in ("level", "host"):
            return True
        return wait.wire == ab.guard_wire and wait.level == 1 - ab.guard_level

    # ------------------------------------------------------------ guards
    def test(self, g, fail, facts, after=None):
        """Code that falls through when guard g holds and jumps to fail otherwise."""
        for atom in conj(g):
            neg = False
            if isinstance(atom, A.Unary) and atom.op == "not":
                atom, neg = atom.operand, True
            if not (isinstance(atom, A.Binary) and atom.op in ("==", "!=") and isinstance(atom.left, A.Ref)):
                raise unsupported("guards other than 'x == c' and 'x != c' joined by 'and'", atom.span)
            eq = (atom.op == "==") != neg
            c = self.sym.eval(atom.right).v
            ref = atom.left
            if ref.name.text in self.sym.wires and ref.index is None:
                self.pin_test(ref.name.text, int(c), eq, fail, after).span = atom.span
            else:
                self.var_test(ref, int(c), eq, fail, facts, atom.span)

    def pin_test(self, wire, v, eq, fail, after):
        want = v if eq else 1 - v
        if self.jmp_pin is None:
            self.jmp_pin = wire
        if wire == self.jmp_pin:
            if want == 0:
                ins = self.jump(fail, "PIN")
            else:
                ok = self.label("ok")
                ins = self.jump(ok, "PIN")
                self.jump(fail)
                self.place(ok)
        else:
            self.need_t0 = True
            ins = self.emit("WAIT", pol=want, src="PIN", idx=self.vin(wire), to=1)
            ins.wait = Wait("test", None, wire, want)
            self.jump(fail, "TOUT")
        ins.reads = wire
        ins.note = f"test {wire} == {want}" + (f" after {printer.event(after)}" if after else "")
        return ins

    def var_test(self, ref, c, eq, fail, facts, span):
        name = ref.name.text
        if "X" in self.counters or "Y" in self.counters:
            raise unsupported("a guard on a variable inside a counted loop (X and Y are taken)", span)
        width = self.var_width(name)
        if facts.osr == name and facts.isr != name and width <= 16:
            self.osr_test(ref, c, eq, fail, facts, span, width)
            return
        if facts.isr != name:
            raise unsupported(f"a guard on '{name}': on some path into this state it is not the "
                              "variable last shifted in (a push or another shift reused the "
                              "register)", span)
        hi = width - 1 if ref.index is None else int(self.sym.eval(ref.index).v)
        lo = hi if ref.index is not None and ref.index_lo is None else \
            (0 if ref.index is None else int(self.sym.eval(ref.index_lo).v))
        if not 0 <= lo <= hi < 32 or c >= 256 or c >= 1 << (hi - lo + 1):
            raise unsupported(f"guard constant {c} on {printer.expr(ref)}", span)
        self.emit("MOV", dst="OSR", src="ISR").note = f"guard {printer.expr(ref)} == {c}"
        facts.osr = None
        if lo:
            self.emit("OUT", dst="NULL", n=lo)
        self.emit("OUT", dst="X", n=hi - lo + 1)
        self.emit("SET", dst="Y", imm=c)
        if eq:
            self.jump(fail, "XNEY")
        else:
            ok = self.label("ok")
            self.jump(ok, "XNEY")
            self.jump(fail)
            self.place(ok)

    def osr_test(self, ref, c, eq, fail, facts, span, width):
        """A guard on the variable the host just sent, still whole in OSR: bits lo to hi are
        copied into X without disturbing OSR (it is saved in ISR around the extraction). When the
        variable is shifted out msb first, the host sends it bit-reversed, so the field sits
        mirrored in OSR and the constant is mirrored too."""
        name = ref.name.text
        hi = width - 1 if ref.index is None else int(self.sym.eval(ref.index).v)
        lo = hi if ref.index is not None and ref.index_lo is None else \
            (0 if ref.index is None else int(self.sym.eval(ref.index_lo).v))
        n = hi - lo + 1
        if not 0 <= lo <= hi < width or c >= 256 or c >= 1 << n:
            raise unsupported(f"guard constant {c} on {printer.expr(ref)}", span)
        pos, want = lo, c
        if self.msb_out(name):
            pos, want = width - 1 - hi, int(f"{c:0{n}b}"[::-1], 2)
        text = f"guard {printer.expr(ref)} == {c}"
        if pos == 0 and n == width:
            self.emit("MOV", dst="X", src="OSR").note = text + " (OSR kept)"
        else:
            if facts.isr is not None:
                raise unsupported(f"a guard on part of '{name}' while '{facts.isr}' is in the receive "
                                  "shift register (the extraction saves OSR there)", span)
            self.emit("MOV", dst="ISR", src="OSR").note = text + " (save OSR)"
            if pos:
                self.emit("OUT", dst="NULL", n=pos)
            self.emit("OUT", dst="X", n=n)
            self.emit("MOV", dst="OSR", src="ISR").note = "restore OSR"
        self.host_notes[f"pull host {name}"] = self.host_notes.get(
            f"pull host {name}", f"bits {width - 1} to 0 hold the value, higher bits must be 0")
        self.emit("SET", dst="Y", imm=want)
        ok = None if eq else self.label("ok")
        self.jump(fail if eq else ok, "XNEY")
        if not eq:
            self.jump(fail)
            self.place(ok)

    def msb_out(self, name):
        """The variable is shifted out msb first somewhere in the role."""
        found = []

        def walk(x):
            if isinstance(x, A.Shift) and x.out and x.var.text == name and x.order == "msb":
                found.append(x)
            if isinstance(x, list):
                for y in x:
                    walk(y)
            elif hasattr(x, "__dataclass_fields__"):
                for k in x.__dataclass_fields__:
                    if k != "span":
                        walk(getattr(x, k))
        walk(self.role.states)
        return bool(found)

    def var_width(self, name):
        v = next(v for v in self.role.vars if v.name.text == name)
        if v.type.width is None:
            return 1
        w = int(self.sym.eval(v.type.width).v)
        return 8 * w if v.type.name.text == "bytes" else w

    def bind_osr(self, name, facts):
        facts.osr = name

    # ------------------------------------------------------------ edges
    def event_edges(self, st, edges, facts):
        ev = edges[0].event
        host = [e for e in edges if is_host_pull(e.event)]
        pins = [e for e in edges if not is_host_pull(e.event)]
        if host and pins and all(isinstance(e, A.OnEdge) for e in edges) and \
                len({printer.event(e.event) for e in pins}) == 1:
            self.host_or_pin(st, host, pins, facts)
            return
        for e in edges[1:]:
            if printer.event(e.event) != printer.event(ev):
                raise unsupported(f"edges on different events in state '{st.name.text}' "
                                  f"({printer.event(ev)} and {printer.event(e.event)})", e.span)
        self.deferred = []
        mark = len(self.prog)
        self.wait_for(ev, facts, None)
        cur = facts.copy()
        for e in edges:
            fail = self.label("next") if e.guard is not None else None
            if e.guard is not None:
                self.test(e.guard, fail, cur, after=ev)
            f = cur.copy()
            ctx = Ctx(anchor=ev, fresh=True, block=True,
                      respond=e if isinstance(e, A.RespondEdge) else None, fail=self.init.name.text)
            self.block(e.body or [], f, ctx)
            self.goto(e.target.text, f)
            if fail is not None:
                self.place(fail)
        if edges[-1].guard is not None:
            self.goto(st.name.text, cur)
        self.trampoline(mark)
        self.flush_deferred()

    def trampoline(self, mark):
        """An edge made of two waits, answered by the very next instruction: the waits share one
        SRAM word, the reaction starts the next, and the state is entered through a jump beside
        the reaction (taken once, on X = 1), so both words are buffered before the first wait.
        Otherwise, when the first wait ends at once, either the second wait or the reaction misses
        the buffer and the reaction can come up to three cycles later."""
        code = [k for k in range(mark, len(self.prog)) if self.prog[k].op != "LABEL"]
        if len(code) < 3 or "X" in self.counters:
            return
        pre, edge, react = (self.prog[k] for k in code[:3])
        if not (pre.wait is not None and pre.wait.kind == "pre" and edge.wait is not None
                and edge.wait.kind == "edge" and react.ob is not None and not react.tune
                and code[1] == code[0] + 1 and code[2] == code[1] + 1
                and self.prog[code[0] - 1].op == "LABEL"):
            return
        again = self.prog[code[0] - 1].label
        pre.align_even, edge.tramp = True, True
        tramp = self.label("enter")
        self.prog.insert(code[2] + 1, Ins("JMP", {"cond": "XDEC", "target": again},
                                          note="taken once, on entry: both words are now buffered"))
        self.prog.insert(code[2] + 1, Ins("LABEL", {}, label=tramp))
        self.prog.insert(code[0] - 1, Ins("JMP", {"cond": "ALWAYS", "target": tramp}))
        self.prog.insert(code[0] - 1, Ins("SET", {"dst": "X", "imm": 1}, note="enter through the jump"))

    def host_or_pin(self, st, host, pins, facts):
        """A state left by a word from the host or by a pin event, whichever comes first: the pin
        waits carry the WAIT alternate "host mailbox full" (FLAG HTXF) and branch to the pull
        when it ends them. If both are there in the same cycle the pin event wins (the WAIT's own
        condition has priority over its alternate)."""
        alt = (1, "FLAG", isa.TABLE_FLAG_HTXF)
        if self.alt is not None and self.alt[:3] != alt:
            raise unsupported("a state waiting for the host or a pin, in a role whose WAIT alternate "
                              "already serves an abort", st.name.span)
        self.alt = alt + (host[0].event,)
        hostlbl = self.label("host")
        ab = Abort(host[0].event, hostlbl, "host")
        self.deferred = []
        ev = pins[0].event
        self.wait_for(ev, facts, Ctx(abort=ab))
        cur = facts.copy()
        for e in pins:
            fail = self.label("next") if e.guard is not None else None
            if e.guard is not None:
                self.test(e.guard, fail, cur, after=ev)
            f = cur.copy()
            self.block(e.body or [], f, Ctx(anchor=ev, fresh=True, block=True, fail=self.init.name.text))
            self.goto(e.target.text, f)
            if fail is not None:
                self.place(fail)
        if pins[-1].guard is not None:
            self.goto(st.name.text, cur)
        self.place(hostlbl)
        for e in host:
            fail = self.label("next") if e.guard is not None else None
            hf = Facts()
            if e.guard is not None:         # not enabled yet: wait again (the word stays there)
                self.test(e.guard, fail, hf)
            self.wait_for(e.event, hf, None)
            self.block(e.body or [], hf, Ctx(fail=self.init.name.text))
            self.goto(e.target.text, hf)
            if fail is not None:
                self.place(fail)
        if host[-1].guard is not None:
            self.goto(st.name.text, Facts())
        self.flush_deferred()

    def after_edges(self, st, edges, facts):
        """`after G` on data only: the guards are tested in order at once; if none holds the
        state is stuck, as in the spec (no clock can make a data guard true later)."""
        self.deferred = []
        cur = facts.copy()
        for e in edges:
            if self.mentions_clock(e.guard):
                raise unsupported("'after' guards on clocks", e.guard.span)
            fail = self.label("next")
            self.test(e.guard, fail, cur)
            f = cur.copy()
            self.block(e.body or [], f, Ctx(fail=self.init.name.text))
            self.goto(e.target.text, f)
            self.place(fail)
        stuck = self.label("stuck")
        self.place(stuck)
        self.jump(stuck).note = "no guard holds: the spec waits here forever"
        self.flush_deferred()

    def mentions_clock(self, g):
        from .fix import names_in
        return bool(names_in(g) & {c.text for c in self.role.clocks})

    def seq(self, st, e, facts):
        self.deferred = []
        ab = self.abort_of(e) if e.abort_on is not None else None
        ctx = Ctx(abort=ab, fail=e.abort_to.text if e.abort_to is not None else self.init.name.text)
        body = e.body
        self.chains = pace.timeline(self.sym, body, self.f)
        for c in self.chains.values():
            if c not in self.chain_list:
                self.chain_list.append(c)
        run = []
        for k, s in enumerate(body):
            if self.strategy == "unrolled" and self.pure(s):
                run.append(s)
                if k + 1 < len(body) and self.pure(body[k + 1]):
                    continue
                self.sorted_run(run, facts, ctx)
                run = []
                continue
            self.next_stmt = body[k + 1] if k + 1 < len(body) else None
            self.stmt(s, facts, ctx)
            if ab is not None and ab.mode == "level" and has_wait(self, s) and \
                    (k + 1 == len(body) or not starts_with_wait(self, body[k + 1])):
                self.jump(ab.target, "TOUT").note = f"abort on {printer.event(ab.event)}"
                self.exits.append((ab.target, Facts()))
        self.chains = {}
        self.goto(e.target.text, facts)
        self.flush_deferred()

    def pure(self, s):
        """A timed statement of a paced chain, or a counted loop of only such statements."""
        if isinstance(s, A.Repeat):
            return all(self.pure(x) for x in s.body)
        return isinstance(s, A.TIMED) and s.timing.window is None and id(s) in self.chains

    def sorted_run(self, run, facts, ctx):
        """Unrolled code for a run of timed statements, issued in the order of their target
        cycles rather than their source order: a sample sees the pin three cycles before an
        output issued with it, so a sample due just before a launch edge issues after it."""
        flat = []

        def expand(stmts):
            for x in stmts:
                if isinstance(x, A.Repeat):
                    for _ in range(int(self.sym.eval(x.count).v)):
                        expand(x.body)
                else:
                    flat.append(x)
        expand(run)
        chain = self.chains[id(flat[0])]
        ent = chain.entries
        start = next(k for k in range(len(ent) - len(flat) + 1)
                     if all(any(x is f for x in ent[k + i].stmts) for i, f in enumerate(flat)))
        units, i = [], 0
        while i < len(flat):
            j = i + 2 if i + 1 < len(flat) and pace.merged_drive(flat[i], flat[i + 1], self.side) \
                else i + 1
            e = ent[start + j - 1]
            units.append((e.ideal - (pace.OUT_OFFSET if e.kind == "out" else 0), i, flat[i:j]))
            i = j
        units.sort(key=lambda u: (u[0], u[1]))
        for n, (_, i, unit) in enumerate(units):
            for j, x in enumerate(unit):
                self.next_stmt = unit[j + 1] if j + 1 < len(unit) else None
                self.entry = start + i + j
                self.pace_hint = None
                if j + 1 == len(unit) and n + 1 < len(units):
                    nxt = units[n + 1]
                    self.pace_hint = pace.issue(ent[start + nxt[1] + len(nxt[2]) - 1]) - \
                        pace.issue(ent[start + i + j])
                self.stmt(x, facts, ctx)
        self.entry = self.pace_hint = None

    def abort_of(self, e):
        ev = e.abort_on
        base, guard = base_event(self.sym, ev)
        target = e.abort_to.text
        if base.kind in ("rise", "fall") and guard is None:
            ab = Abort(ev, target, "level")
            alt = (1 if base.kind == "rise" else 0, "PIN", self.vin(base.name.text))
        elif base.kind in ("rise", "fall", "edge") and guard is not None:
            from .semantics import level_guard
            lv = level_guard(self.sym, guard)
            if not lv or len(lv) != 1:
                raise unsupported(f"abort on {printer.event(ev)}: its guard must be one 'w == 0|1'",
                                  ev.span)
            (gw, gl), = lv.items()
            ab = Abort(ev, target, "change", base.name.text,
                       None if base.kind == "edge" else (1 if base.kind == "rise" else 0))
            ab.guard_wire, ab.guard_level = gw, gl
            alt = (1, "PIN", 4 + self.vin(base.name.text))
        else:
            raise unsupported(f"abort on {printer.event(ev)}", ev.span)
        if self.alt is not None and self.alt[:3] != alt:
            raise unsupported("two different abort conditions; the lane has one WAIT alternate", ev.span)
        self.alt = alt + (ab.event,)
        return ab

    def flush_deferred(self):
        """Out-of-line abort checks: the alternate fired; abort if it was the event, else wait again."""
        for chk, ab, ins in self.deferred:
            self.place(chk)
            if ab.check_level is None:
                self.jump(ab.target).note = f"abort on {printer.event(ab.event)}"
            else:
                back = self.label("rewait")
                self.pin_test(ab.check_wire, ab.check_level, True, back, None).abort_check = True
                self.jump(ab.target).note = f"abort on {printer.event(ab.event)}"
                self.place(back)
                again = Ins(ins.op, dict(ins.a), wait=ins.wait, note="wait again: not the abort event")
                again.rewait = True
                self.prog.append(again)
                self.jump(self.label_after(ins))
            self.exits.append((ab.target, Facts()))
        self.deferred = []

    def label_after(self, ins):
        """A label on the instruction after ins (created on demand)."""
        i = self.prog.index(ins)
        if self.prog[i + 1].op == "LABEL":
            return self.prog[i + 1].label
        name = self.label("resume")
        self.prog.insert(i + 1, Ins("LABEL", {}, label=name))
        return name

    # ------------------------------------------------------------ statements
    def block(self, body, facts, ctx):
        for k, s in enumerate(body):
            self.next_stmt = body[k + 1] if k + 1 < len(body) else None
            self.stmt(s, facts, ctx)

    def stmt(self, s, facts, ctx):
        if isinstance(s, A.TIMED):
            self.timed(s, facts, ctx)
        elif isinstance(s, A.Await):
            if s.timeout is not None:
                raise unsupported("'await ... timeout'", s.timeout.span)
            again = self.wait_for(s.event, facts, ctx, force_pre=s.guard is not None)
            if s.guard is not None:
                self.test(s.guard, again, facts, after=s.event)
            ctx.anchor, ctx.fresh = s.event, True
        elif isinstance(s, A.Repeat):
            self.repeat(s, facts, ctx)
        elif isinstance(s, A.If):
            other, end = self.label("else"), self.label("endif")
            self.test(s.guard, other, facts)
            f1 = facts.copy()
            self.block(s.then, f1, ctx)
            self.jump(end)
            self.place(other)
            f2 = facts.copy()
            self.block(s.orelse or [], f2, ctx)
            self.place(end)
            m = f1.meet(f2)
            facts.levels, facts.isr, facts.osr = m.levels, m.isr, m.osr
        elif isinstance(s, A.Wait):
            ticks = self.sym.eval(s.time).v * self.f
            d = self.emit("DLY", d=0)
            d.tune, d.note = True, f"wait {printer.expr(s.time)}"
            ref = self.last_effect if self.last_effect is not None else (d, None)
            self.pending_sep = (ref, ticks, s.span, d, printer.stmt(s, 0))
        elif isinstance(s, A.HostIO):
            if s.pull:
                self.wait_for(A.Event("named", A.Ident("host"), A.Ident("pull"), [A.Ref(s.var)]),
                              facts, ctx)
                ctx.anchor = None
            else:
                if facts.isr != s.var.text:
                    raise unsupported(f"push host {s.var.text}: it is not the variable in the receive "
                                      "shift register", s.span)
                self.action(self.emit("PUSH", blk=0), None)
                self.host_notes[f"push host {s.var.text}"] = (
                    f"mailbox bits {self.var_width(s.var.text) - 1} to 0, first bit received highest; "
                    "higher bits are not defined; the push does not block, so the host must empty the "
                    "mailbox before the next push")
                facts.isr = None
        elif isinstance(s, A.Emit):
            if s.args:
                raise unsupported("emit with arguments", s.span)
            code = self.emit_codes.setdefault(s.name.text, len(self.emit_codes))
            if code > 0x3D:
                raise unsupported("more than 62 emitted event names", s.span)
            self.action(self.emit("EMIT", code=code), None).note = f"emit {s.name.text}"
        else:
            raise unsupported(f"'{type(s).__name__.lower()}' in a state machine", s.span)

    def timed(self, s, facts, ctx):
        chain = self.chains.get(id(s))
        if chain is None and pace.paced_stmt(s):
            span = (s.timing.for_ or s.timing.tol or getattr(s, "each", None)).span
            raise unsupported("'for', 'each' and '~' outside a seq", span)
        if pace.merged_drive(s, self.next_stmt, self.side):
            self.pending_side[s.wire.text] = self.drive_level(s)
            if chain is not None:
                self.pending_paces.append(Pace(chain, s, self.entry))
            return
        win = s.timing.window
        ob = None
        if win is not None and win.after.kind in ("rise", "fall", "edge") and \
                self.wires.get(win.after.name.text) is not None and \
                self.wires[win.after.name.text].dir == "out":
            self.own_edge_window(s, win, facts, ctx)
            return
        if win is not None:
            ev = win.after
            same = ctx.anchor is not None and printer.event(ctx.anchor) == printer.event(ev)
            if not (same and (ctx.fresh or ctx.block)):
                self.wait_for(ev, facts, ctx)
                ctx.anchor, ctx.fresh = ev, True
            if not ctx.block:
                ctx.fresh = False
            ob = self.window_ob(s, win.lo, win.hi, ev, printer.stmt(s, 0).rstrip(";"))
        elif ctx.respond is not None:
            r = ctx.respond
            ob = self.window_ob(s, r.lo, r.hi, r.event, f"respond {r.name.text}")
        if ob is not None and ob.lo > 0:
            d = self.emit("DLY", d=0)
            d.tune, d.note = True, "place the response in its window"
            self.pending_tune = d
        else:
            self.pending_tune = None
        lock = chain is not None and not self.counters and self.starts_chain(chain, s)
        if lock:
            self.emit("DLY", d=0).note = "phase lock: the next word is fetched in this lane's slot"
        ins = self.action_for(s, facts, ctx)
        ins.ob = ob
        if lock:
            ins.align_even = ins.lock = True
        if self.pending_tune is not None:
            self.pending_tune.serves = ins
        if chain is not None:
            self.paced(ins, s, chain)

    def starts_chain(self, chain, s):
        """s (or a drive merged into it) is the first action of its chain. Its instruction then
        starts an SRAM word after a 1-cycle instruction, so it issues at a known slot phase and
        the rest of the chain is timed exactly from it."""
        firsts = [(p.chain, p.stmt, p.entry) for p in self.pending_paces] + [(chain, s, self.entry)]
        return any(e == 0 or (e is None and 0 in c.index(x)) for c, x, e in firsts)

    def paced(self, ins, s, chain):
        """Tag the action with its place in the timeline and give it the delay that sets the
        distance to the next action (solved later); it starts as the statement's own gap, which
        is what the last action of a chain must hold."""
        ins.paces = self.pending_paces + [Pace(chain, s, self.entry)]
        self.pending_paces = []
        k = chain.index(s)[0] if self.entry is None else self.entry
        e = chain.entries[k]
        if self.pace_hint is not None:          # reordered code: the distance to the next issue
            hold = self.pace_hint - 2
        elif k + 1 < len(chain.entries):
            hold = pace.issue(chain.entries[k + 1]) - pace.issue(e) - 2
        else:                                   # the last action holds for its gap
            hold = math.ceil(pace.Timeline(self.sym, self.f).gap(s) - e.tol) - 2
        extra = max(0, hold + 1)                # cycles beyond the action's own one
        if getattr(ins, "pace_self", False) and extra > isa.DLY_MAX:
            ins.a["d"], extra = isa.DLY_MAX, extra - isa.DLY_MAX
            ins.pace_self = False
        if getattr(ins, "pace_self", False):    # a side-set DLY: its own length
            ins.a["d"], ins.pace_dly, ins.pace_field = extra, ins, "d"
        elif ins.op in ("OUT", "IN", "MOV") and extra <= 7:
            ins.a["delay"], ins.pace_dly, ins.pace_field = extra, ins, "delay"
        else:
            if ins.op in ("OUT", "IN", "MOV"):
                ins.a["delay"], extra = 7, extra - 7
            while extra - 1 > isa.DLY_MAX:      # long gaps: whole 512-cycle DLYs, then the tuned one
                self.emit("DLY", d=isa.DLY_MAX).note = "pace: long gap (9-bit DLY field)"
                extra -= isa.DLY_MAX + 1
            d = self.emit("DLY", d=extra - 1)
            d.optional, d.note = True, "pace: distance to the next timed action"
            ins.pace_dly, ins.pace_field = d, "d"
        if extra < 3 and (self.pace_hint is not None if self.entry is not None
                          else k + 1 < len(chain.entries)):
            ins.align_even = True               # short: it and the next action share a word
    def own_edge_window(self, s, win, facts, ctx):
        """A window after an edge we make ourselves is a distance between two of our own
        instructions: the one that drove the wire last, and this one."""
        w = win.after.name.text
        ref = self.last_drive.get(w)
        if ref is None:
            raise unsupported(f"a window after {printer.event(win.after)}, but nothing before it "
                              f"drives '{w}'", win.span)
        lo, hi = (self.sym.eval(x).v * self.f for x in (win.lo, win.hi))
        d = None
        out = isinstance(s, (A.Drive, A.Release)) or (isinstance(s, A.Shift) and s.out)
        if lo > 0 or not out:           # a sample sees the pin only 3 cycles after its issue
            d = self.emit("DLY", d=0)
            d.tune, d.note = True, "place the action after our own edge"
        ins = self.action_for(s, facts, ctx)
        mine = s.wire.text if out else None
        ins.sep = Sep(ref, lo, s.span, printer.stmt(s, 0).rstrip(";"), skew=mine != w, hi=hi,
                      kind="out" if out else "in")
        if d is not None:
            d.serves = ins

    def window_ob(self, s, lo, hi, ev, source):
        out = isinstance(s, (A.Drive, A.Release)) or (isinstance(s, A.Shift) and s.out)
        name = name_of(s)
        return WinOb("OB-WIN", name, s.span, "out" if out else "in", ev,
                     self.sym.eval(lo).v * self.f, self.sym.eval(hi).v * self.f, lo, hi, source)

    def drive_level(self, s):
        return 1 if isinstance(s, A.Release) else int(self.sym.eval(s.value).v)

    def action_for(self, s, facts, ctx):
        if isinstance(s, (A.Drive, A.Release)):
            w = s.wire.text
            d = self.wires[w]
            lvl = self.drive_level(s)
            m = 1 << self.vout(w)
            text = printer.stmt(s, 0).rstrip(";")
            if w in self.side:                  # side-set: any instruction carries the level
                ins = self.emit("DLY", d=0)
                ins.side, ins.pace_self, ins.note = {w: lvl}, True, text + " (side-set)"
                return self.action(ins, w)
            if d.dir == "inout" and not d.open_drain:      # turnaround: the direction does the work
                if isinstance(s, A.Release):
                    ins = self.emit("SET", dst="PINDIRS", imm=m << 4)
                else:
                    self.emit("SET", dst="PINS", imm=m << 4 | (m if lvl else 0)).note = "level first"
                    ins = self.emit("SET", dst="PINDIRS", imm=m << 4 | m)
                ins.note = text
                return self.action(ins, w)
            ins = self.emit("SET", dst="PINS", imm=m << 4 | (m if lvl else 0))
            ins.note = text + (" (open drain)" if d.open_drain else "")
            return self.action(ins, w)
        if isinstance(s, A.Shift):
            n = int(self.sym.eval(s.count).v)
            if n != len(s.wires) or (n > 1 and not s.out):
                raise unsupported("a shift of more than one bit per pin, or into more than one pin, "
                                  "in a state machine", s.span)
            v = s.var.text
            if s.out:
                if facts.osr not in (v, v + "~"):
                    raise unsupported(f"shift of '{v}': it is not in the transmit shift register "
                                      "(pull it from the host first; a guard reuses the register)", s.span)
                facts.osr = v + "~"             # partly shifted out: no longer whole for a guard
                ins = self.emit("OUT", dst="PINS", n=n)
                if s.order == "msb":
                    self.host_notes[f"pull host {v}"] = ("send the word bit-reversed: OUT shifts bit 0 "
                                                         "first, and the spec sends the msb first")
                return self.action(ins, s.wires[0].text)
            ins = self.emit("IN", src="PINS", n=1)
            if facts.isr not in (None, v):
                raise unsupported(f"shift into '{v}' while '{facts.isr}' is still in the receive "
                                  "shift register", s.span)
            facts.isr = v
            if s.order == "lsb":
                self.host_notes[f"shift into {v}"] = "bits arrive msb first in the register; reverse them"
            return self.action(ins, None)
        if s.expect is None:
            ins = self.action(self.emit("DLY", d=0), None)
            ins.note = f"sample {s.wire.text} (value unused)"
            return ins
        before = len(self.prog)
        self.pin_test(s.wire.text, int(self.sym.eval(s.expect).v), True, ctx.fail, None)
        self.exits.append((ctx.fail, Facts()))
        return next(i for i in self.prog[before:] if i.reads)

    def action(self, ins, wire):
        if self.pending_side:
            ins.side = {**self.pending_side, **(getattr(ins, "side", None) or {})}
            for w in self.pending_side:
                self.last_drive[w] = ins
            self.pending_side = {}
        if self.pending_sep is not None:
            ref, ticks, span, dly, text = self.pending_sep
            if ref is not None:
                ins.sep = Sep(ref[0], ticks, span, text, skew=ref[1] is not None and ref[1] != wire)
                dly.serves = ins
            self.pending_sep = None
        if wire is not None:
            self.last_effect = (ins, wire)
            self.last_drive[wire] = ins
        return ins

    def repeat(self, s, facts, ctx):
        n = int(self.sym.eval(s.count).v)
        if self.strategy == "unrolled" or n == 1:
            for _ in range(n):
                self.block(s.body, facts, ctx)
            return
        reg = next((r for r in ("Y", "X") if r not in self.counters), None)
        if reg is None or n - 1 > 255:
            raise unsupported("a counted loop needs X or Y and at most 256 iterations", s.span)
        def body_ctx():         # every iteration starts without a fresh occurrence
            return Ctx(None, False, False, ctx.respond, ctx.abort, ctx.fail)

        end = self.trial(s.body, facts, body_ctx(), reg)
        self.emit("SET", dst=reg, imm=n - 1).note = f"repeat {n}"     # before any wait: off the path
        for w, lvl in sorted(trial_levels_to_peel(self, s, facts, end).items()):
            ev = A.Event("fall" if lvl == 0 else "rise", A.Ident(w))
            self.wait_ins(lvl, self.vin(w), Wait("pre", ev, w, lvl), ctx).note = "level before the loop"
            facts.levels[w] = lvl
        head = facts.copy()
        for _ in range(4):
            new = head.meet(self.trial(s.body, head, body_ctx(), reg))
            if new.key() == head.key():
                break
            head = new
        top = self.label("loop")
        self.place(top)
        self.prog[-1].align = True
        inner = head.copy()
        self.counters.append(reg)
        self.block(s.body, inner, body_ctx())
        self.counters.pop()
        self.jump(top, f"{reg}DEC")
        facts.levels, facts.isr, facts.osr = inner.levels, inner.isr, inner.osr

    def trial(self, body, facts, ctx, reg):
        """Facts at the end of one pass of body, without keeping its code."""
        mark = len(self.prog)
        saved = (self.nlabel, list(self.exits), self.last_effect, list(self.deferred),
                 self.pending_sep, dict(self.last_drive), dict(self.pending_side),
                 list(self.pending_paces), self.next_stmt)
        out = facts.copy()
        self.counters.append(reg)
        self.block(body, out, ctx)
        self.counters.pop()
        del self.prog[mark:]
        (self.nlabel, self.exits, self.last_effect, self.deferred, self.pending_sep, self.last_drive,
         self.pending_side, self.pending_paces, self.next_stmt) = saved
        return out


def trial_levels_to_peel(fsm, s, entry, end):
    """Wires whose level the loop body leaves known at its end, where the body's first use of the
    wire waits for an edge that needs exactly that level first: waiting for it once before the loop
    lets every iteration skip that wait."""
    out = {}
    for w, lvl in end.levels.items():
        if entry.levels.get(w) == lvl:
            continue
        first = first_edge(fsm, s.body, w)
        if first is not None and first == ("rise" if lvl == 0 else "fall"):
            out[w] = lvl
    return out


def first_edge(fsm, body, wire):
    for st in body:
        evs = []
        if isinstance(st, A.Await):
            evs = [st.event]
        elif isinstance(st, A.TIMED) and st.timing.window is not None:
            evs = [st.timing.window.after]
        elif isinstance(st, A.Repeat):
            k = first_edge(fsm, st.body, wire)
            if k is not None:
                return k
        for ev in evs:
            base, _ = base_event(fsm.sym, ev)
            if base.kind in ("rise", "fall") and base.name.text == wire:
                return base.kind
    return None


def meet_osr(a, b):
    """OSR holds v whole, or v~ once some of it has been shifted out; whole meets shifted as
    shifted, anything else as unknown."""
    if a == b:
        return a
    if a is not None and b is not None and a.rstrip("~") == b.rstrip("~"):
        return a.rstrip("~") + "~"
    return None


def is_host_pull(ev):
    return ev.kind == "named" and ev.name.text == "host" and ev.attr is not None and \
        ev.attr.text == "pull"


def same_facts(a, b):
    return (a is None) == (b is None) and (a is None or a.key() == b.key())


def conj(g):
    if isinstance(g, A.Binary) and g.op == "and":
        return conj(g.left) + conj(g.right)
    return [g]


def has_wait(fsm, s):
    if isinstance(s, A.Await) or (isinstance(s, A.HostIO) and s.pull):
        return True
    if isinstance(s, A.TIMED) and s.timing.window is not None:
        return True
    if isinstance(s, (A.Repeat,)):
        return any(has_wait(fsm, x) for x in s.body)
    if isinstance(s, A.If):
        return any(has_wait(fsm, x) for x in s.then + (s.orelse or []))
    return False


def starts_with_wait(fsm, s):
    if isinstance(s, A.Repeat):
        return bool(s.body) and starts_with_wait(fsm, s.body[0])
    return isinstance(s, A.Await) or (isinstance(s, A.TIMED) and s.timing.window is not None)


def name_of(s):
    kind = {A.Drive: "drive", A.Release: "release", A.Shift: "shift", A.Sample: "sample"}
    return f"{kind[type(s)]} {s.wire.text}"


def layout(prog):
    """Drop label markers, pad so loop heads and reacting waits start an SRAM word, resolve labels.
    Returns (instructions, labels)."""
    prog = drop_fallthrough(prog)
    out, labels, pending = [], {}, []
    for k, ins in enumerate(prog):
        if ins.op == "DLY" and getattr(ins, "optional", False) and ins.a["d"] < 0:
            continue                    # a pacing delay the solver did not need
        if ins.op == "LABEL":
            pending.append(ins.label)
            if getattr(ins, "align", False):
                pending.append(None)
            continue
        align = None in pending or getattr(ins, "align_even", False) or \
            (ins.op == "WAIT" and ins.wait is not None and ins.wait.kind == "edge"
             and not getattr(ins, "tramp", False) and reacts(prog, k))
        if align and len(out) % 2:
            pad = Ins("DLY", {"d": 0}, note="pad: start an SRAM word")
            out.append(pad)
        names = [p for p in pending if p is not None]
        for name in names:
            labels[name] = len(out)
        if names:       # show a state name rather than a generated one
            ins.label = next((n for n in names if not n[-1].isdigit()), names[0])
        pending = []
        out.append(ins)
    for name in [p for p in pending if p is not None]:
        labels[name] = len(out)
    return out, labels


def drop_fallthrough(prog):
    """A JMP ALWAYS to the label right after it costs a cycle and a fetch for nothing."""
    out = []
    for k, ins in enumerate(prog):
        if ins.op == "JMP" and ins.a["cond"] == "ALWAYS":
            j, names = k + 1, set()
            while j < len(prog) and prog[j].op == "LABEL":
                names.add(prog[j].label)
                j += 1
            if ins.a["target"] in names:
                continue
        out.append(ins)
    return out


def reacts(prog, k):
    """The instruction after the wait at k discharges a window: it should share the wait's word."""
    for ins in prog[k + 1:]:
        if ins.op == "LABEL":
            continue
        return ins.ob is not None or (ins.tune and ins.serves is not None)
    return False
