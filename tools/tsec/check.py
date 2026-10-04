"""Stage 3b, semantic checks: a grammatical spec can still be meaningless.

Walks every role with the symbol table from resolve and collects all errors,
so one run reports them together, sorted by position.

  E201 role uses an unknown wire       E211 `for`, `each`, `wait`, timeout need a time
  E202 edge targets an unknown state   E212 `~` needs a ratio or a time
  E203 pin event on an output wire     E213 window must be [a, b] with times 0 <= a < b
  E204 unknown wire                    E214 state unreachable from the initial state
  E205 wire not in the role's `uses`   E215 duplicate name
  E206 driving an input wire           E216 count must be a positive whole number
  E207 sampling an output wire         E217 role has no states, or two initial states
  E208 `drive 1` on an open-drain wire E218 `release` needs an open-drain wire
  E209 unknown variable                E219 a wire level is 0 or 1
  E210 shift count larger than the variable

The spec-level checks (overlapping edges, timelock, datasheet contradictions,
frames, enums, triggers) are in speccheck.py, the assist checks in assists.py.
check() returns the warnings and raises Errors for the errors.
"""
from . import ast as A
from . import assists, speccheck
from .diag import Errors, TseError
from .units import dimname

HOST_EVENTS = ("pull", "push", "ready", "window_loaded")


class RoleChecker:
    def __init__(self, sym, role, errs):
        self.sym, self.role, self.errs = sym, role, errs
        self.vars = {}
        self.states = {}
        self.clocks = {c.text for c in getattr(role, "clocks", [])}
        self.emits = []

    def err(self, code, msg, span):
        self.errs.append(TseError(code, msg, span))

    def q(self, e):
        try:
            return self.sym.eval(e)
        except TseError as x:
            self.errs.append(x)
            return None

    def need_time(self, e, what, span):
        q = self.q(e)
        if q is not None and q.dim != 1:
            self.err("E211", f"'{what}' needs a time, got a {dimname(q.dim)}", span)
        return q

    def need_count(self, e, what):
        q = self.q(e)
        if q is not None and (q.dim != 0 or q.v.denominator != 1 or q.v < 1):
            self.err("E216", f"{what} must be a positive whole number", e.span)
            return None
        return q

    def need_level(self, e):
        q = self.q(e)
        if q is not None and (q.dim != 0 or q.v not in (0, 1)):
            self.err("E219", "a wire level is 0 or 1", e.span)
            return None
        return q

    # ------------------------------------------------------------ names
    def wire(self, ident):
        w = self.sym.wires.get(ident.text)
        if w is None:
            self.err("E204", f"unknown wire '{ident.text}'", ident.span)
            return None
        if ident.text not in [u.text for u in self.role.uses]:
            self.err("E205", f"wire '{ident.text}' is not in the 'uses' list of role "
                     f"'{self.role.name.text}'", ident.span)
        return w

    def var(self, ident):
        v = self.vars.get(ident.text)
        if v is None:
            self.err("E209", f"unknown variable '{ident.text}'", ident.span)
        return v

    def guard(self, e):
        """Guards may name wires, variables, clocks, params and units."""
        if isinstance(e, A.Ref):
            name = e.name.text
            if name in self.clocks and e.index is None:
                return
            if name in self.vars or name in self.sym.wires:
                if name in self.sym.wires:
                    self.wire(e.name)
                for sub in (e.index, e.index_lo):
                    if sub is not None:
                        self.q(sub)
                return
            self.q(e)
        elif isinstance(e, A.Unary):
            self.guard(e.operand)
        elif isinstance(e, A.Binary):
            self.guard(e.left)
            self.guard(e.right)
        elif isinstance(e, (A.Num, A.Call)):
            self.q(e)

    def event(self, ev, outgoing_ok=False):
        if ev.kind != "named":
            w = self.wire(ev.name)
            if w is not None and w.dir == "out" and not outgoing_ok:
                self.err("E203", f"event {ev.kind}({ev.name.text}) needs an input wire",
                         ev.name.span)
            return
        if ev.name.text == "host" and ev.attr is not None:
            if ev.attr.text not in HOST_EVENTS:
                self.err("E101", f"unknown host event 'host.{ev.attr.text}'", ev.attr.span)
            for a in ev.args:
                if isinstance(a, A.Ref):
                    self.var(a.name)
            return
        if ev.attr is None and ev.name.text in self.sym.events:
            return
        if ev.attr is not None and ev.name.text in self.sym.assists:    # crc16.ok, bitclk.tick
            return
        self.err("E101", f"unknown event '{ev.name.text}'", ev.name.span)

    # ------------------------------------------------------------ statements
    def timing(self, t, span):
        if t.window:
            w = t.window
            lo, hi = self.q(w.lo), self.q(w.hi)
            self.event(w.after, outgoing_ok=True)      # a window may follow our own edge
            if lo is not None and hi is not None and (lo.dim != 1 or hi.dim != 1
                                                      or not 0 <= lo.v < hi.v):
                self.err("E213", "window must be [a, b] with times 0 <= a < b", w.span)
        if t.for_ is not None:
            self.need_time(t.for_, "for", span)
        if t.tol is not None:
            q = self.q(t.tol)
            if q is not None and q.dim not in (0, 1):
                self.err("E212", "'~' needs a ratio or a time", t.tol.span)

    def stmt(self, s):
        if isinstance(s, A.Drive):
            w = self.wire(s.wire)
            level = self.need_level(s.value)
            if w is not None:
                if w.dir == "in":
                    self.err("E206", f"cannot drive '{w.name.text}': it is declared 'in'",
                             s.wire.span)
                elif w.open_drain and level is not None and level.v == 1:
                    self.err("E208", f"'{w.name.text}' is open drain: write 'release "
                             f"{w.name.text}' instead of driving 1", s.wire.span)
            self.timing(s.timing, s.span)
        elif isinstance(s, A.Release):
            w = self.wire(s.wire)
            if w is not None and w.dir == "in":
                self.err("E206", f"cannot release '{w.name.text}': it is declared 'in'",
                         s.wire.span)
            elif w is not None and not w.open_drain and w.dir != "inout":
                self.err("E218", f"'release' needs an open-drain or inout wire; '{w.name.text}' "
                         "is a push-pull output", s.wire.span)
            self.timing(s.timing, s.span)
        elif isinstance(s, A.Shift):
            for ident in s.wires:
                w = self.wire(ident)
                if w is not None and s.out and w.dir == "in":
                    self.err("E206", f"cannot drive '{ident.text}': it is declared 'in'", ident.span)
                if w is not None and not s.out and w.dir == "out":
                    self.err("E207", f"cannot sample '{ident.text}': it is declared 'out'",
                             ident.span)
            v = self.var(s.var)
            n = self.need_count(s.count, "a shift count")
            width = self.width(v)
            if n is not None and width is not None and n.v > width:
                self.err("E210", f"shift of {n.v} bits into bits[{width}]", s.span)
            if s.each is not None:
                self.need_time(s.each, "each", s.span)
            self.timing(s.timing, s.span)
        elif isinstance(s, A.Sample):
            w = self.wire(s.wire)
            if w is not None and w.dir == "out":
                self.err("E207", f"cannot sample '{s.wire.text}': it is declared 'out'", s.wire.span)
            if s.expect is not None:
                self.need_level(s.expect)
            self.timing(s.timing, s.span)
        elif isinstance(s, A.Await):
            self.event(s.event)
            if s.guard is not None:
                self.guard(s.guard)
            if s.timeout is not None:
                self.need_time(s.timeout, "timeout", s.span)
        elif isinstance(s, A.Repeat):
            self.need_count(s.count, "a repeat count")
            self.block(s.body)
        elif isinstance(s, A.If):
            self.guard(s.guard)
            self.block(s.then)
            if s.orelse is not None:
                self.block(s.orelse)
        elif isinstance(s, A.Wait):
            self.need_time(s.time, "wait", s.span)
        elif isinstance(s, A.HostIO):
            self.var(s.var)
        elif isinstance(s, A.Set):
            self.var(s.name)
            self.q(s.value)
        elif isinstance(s, A.Reset):
            for n in s.names:
                if n.text not in self.clocks:
                    self.var(n)
        elif isinstance(s, A.Emit):
            self.emits.append(s.name)
            for a in s.args:
                self.guard(a)
        elif isinstance(s, A.Send):
            if s.name.text not in self.sym.frames:
                self.err("E101", f"unknown frame '{s.name.text}'", s.name.span)
            for a in s.args:
                self.guard(a)
            self.timing(A.Timing(s.window), s.span)
        elif isinstance(s, A.Recv):
            if s.name.text not in self.sym.frames:
                self.err("E101", f"unknown frame '{s.name.text}'", s.name.span)
            if s.into is not None:
                self.var(s.into)

    def width(self, v):
        if v is None or v.type.width is None:
            return None if v is None or v.type.name.text not in ("bit", "bool") else 1
        q = self.q(v.type.width)
        return int(q.v) if q is not None else None

    def block(self, body):
        for s in body or ():
            self.stmt(s)

    # ------------------------------------------------------------ role
    def target(self, ident):
        if ident is not None and ident.text not in self.states:
            self.err("E202", f"state '{ident.text}' does not exist in role "
                     f"'{self.role.name.text}'", ident.span)

    def run(self):
        role = self.role
        for u in role.uses:
            if u.text not in self.sym.wires:
                self.err("E201", f"role '{role.name.text}' uses unknown wire '{u.text}'", u.span)
        for v in role.vars:
            if v.name.text in self.vars:
                self.err("E215", f"'{v.name.text}' is already declared at line "
                         f"{self.vars[v.name.text].span.line}", v.name.span)
            self.vars[v.name.text] = v
            if v.type.width is not None:
                self.need_count(v.type.width, "a width")
        for a in role.assumes:
            self.event(a.a)
            if a.b is not None:
                self.event(a.b)
                self.need_time(a.bound, "assume", a.name.span)
        for st in role.states:
            if st.name.text in self.states:
                self.err("E215", f"state '{st.name.text}' is already declared at line "
                         f"{self.states[st.name.text].span.line}", st.name.span)
            self.states.setdefault(st.name.text, st)
        initial = [st for st in role.states if st.initial]
        if not role.states:
            self.err("E217", f"role '{role.name.text}' has no states", role.name.span)
            return
        if len(initial) > 1:
            self.err("E217", f"role '{role.name.text}' has two initial states", initial[1].name.span)
        for st in role.states:
            if st.inv is not None:
                self.guard(st.inv)
            for e in st.edges:
                self.target(e.target)
                if isinstance(e, (A.OnEdge, A.RespondEdge)):
                    self.event(e.event)
                if isinstance(e, A.SeqEdge) and e.abort_on is not None:
                    self.event(e.abort_on)
                    self.target(e.abort_to)
                if getattr(e, "guard", None) is not None:
                    self.guard(e.guard)
                if isinstance(e, A.RespondEdge):
                    lo, hi = self.q(e.lo), self.q(e.hi)
                    if lo is not None and hi is not None and (lo.dim != 1 or hi.dim != 1
                                                              or not 0 <= lo.v < hi.v):
                        self.err("E213", "window must be [a, b] with times 0 <= a < b", e.name.span)
                self.block(e.body)
        start = initial[0] if initial else role.states[0]
        seen, todo = {start.name.text}, [start]
        while todo:
            for e in todo.pop().edges:
                for t in (e.target, getattr(e, "abort_to", None)):
                    if t is not None and t.text in self.states and t.text not in seen:
                        seen.add(t.text)
                        todo.append(self.states[t.text])
        for st in role.states:
            if st.name.text not in seen:
                self.err("E214", f"state '{st.name.text}' is unreachable", st.name.span)


def check(sym):
    errs = []
    every_wire = [w.name for w in sym.wires.values()]   # spec-level rules may name any wire
    for it in sym.spec.items:
        if isinstance(it, A.Wire):
            idle = it.attr("idle")
            if idle is not None:
                RoleChecker(sym, None, errs).need_level(idle.value)
        elif isinstance(it, A.Datasheet):
            for r in it.rules:
                rc = RoleChecker(sym, A.Role(r.name, "monitor", every_wire), errs)
                rc.event(r.a)
                rc.event(r.b)
                rc.need_time(r.bound, "rule", r.name.span)
                if r.guard is not None:
                    rc.guard(r.guard)
        elif isinstance(it, A.EventDef):
            rc = RoleChecker(sym, A.Role(it.name, "monitor", every_wire), errs)
            rc.event(it.event)
            if it.guard is not None:
                rc.guard(it.guard)
    sym.emits = {}
    for role in sym.roles:
        rc = RoleChecker(sym, role, errs)
        rc.run()
        sym.emits[role.name.text] = rc.emits
    warns = []
    if not errs:            # the spec-level checks assume names and dimensions are sound
        speccheck.run(sym, errs, warns)
        assists.check(sym, errs)
    if errs:
        raise Errors(errs)
    return sorted(warns, key=lambda e: (e.span.line, e.span.col))
