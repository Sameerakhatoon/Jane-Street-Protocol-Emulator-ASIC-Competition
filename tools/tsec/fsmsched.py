"""Stage 5d, scheduling a state machine: layout, delay solving, rule R1 diagnostics.

For each lowering strategy (rolled loops first, then unrolled), the program
is laid out, every tunable DLY is set to the least value that puts the
response it serves at the earliest legal point plus one tick of safety (or
gives a `wait` its distance), and the explicit-state analysis (fsmtime.py)
then proves or refutes every obligation. Each failure becomes a full
rejection: what was required, what the program achieves, the path, how late
or early, and a fix solved for the params of the bound (fix.py).
"""
import math
from dataclasses import dataclass, field
from fractions import Fraction as Fr

from . import fix, isa, printer
from .diag import TseError
from .fsm import STRATEGIES, Fsm, layout
from .fsmtime import Analysis, stability_warnings


@dataclass
class FsmAttempt:
    mode: str
    fsm: Fsm
    prog: list
    labels: dict
    res: object = None
    violations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    hint: str = ""

    @property
    def low(self):
        return self

    @property
    def report(self):
        return self


def ticks_ns(t, fclk):
    return f"{float(t / fclk * 10**9):.1f} ns"


class Scheduler:
    def __init__(self, sym, role, lane, fclk, fm, pads):
        self.sym, self.role, self.lane, self.f, self.fm = sym, role, lane, fclk, fm
        self.pads = (Fr(pads[0]) * fclk / 10**9, Fr(pads[1]) * fclk / 10**9)   # ns to ticks
        self.pads_ns = pads

    def analyse(self, att):
        targets = [ins.a["target"] for ins in att.prog if ins.op == "JMP" and ins.a["cond"] == "TOUT"]
        a = Analysis(self.sym, self.role, att.prog, att.labels, self.lane, self.fm, self.f, targets)
        return a.run()

    # ------------------------------------------------------------ achieved intervals
    def achieved(self, ob, st):
        """Ticks at the pins after the pin edge: [lo, hi]."""
        pmin, pmax = self.pads
        if ob.kind == "out":
            return st.dmin + 3 + pmin, st.dmax + 4 + pmax
        return st.dmin + pmin, st.dmax + 1 + pmax

    def need(self, ins, safety):
        """Least value the served measure must reach: (kind, value)."""
        if ins.ob is not None:
            lo = ins.ob.lo + safety
            base = 3 if ins.ob.kind == "out" else 0
            return "dmin", math.ceil(lo - self.pads[0] - base)
        if ins.sep is not None:
            return "sep", math.ceil(self.sep_bounds(ins.sep)[0])
        return None, None

    def sep_bounds(self, sep):
        """(least, largest) issue-to-issue cycles that keep a distance inside its bounds at the
        pins: outputs differ by the pad skew; a sample sees the pin 3 cycles and a pad earlier."""
        pmin, pmax = self.pads
        if sep.kind == "in":
            lo, shift_hi = sep.ticks + 3 + pmax, 3 + pmin
        else:
            skew = pmax - pmin if sep.skew else 0
            lo, shift_hi = sep.ticks + skew, -skew
        return lo, (None if sep.hi is None else sep.hi + shift_hi)

    def sep_fails(self, ins, got):
        lo, hi = self.sep_bounds(ins.sep)
        return got[1] < lo or (hi is not None and got[3] > hi)

    def have(self, res, ins):
        if ins.ob is not None:
            st = res.obs.get(id(ins))
            return None if st is None or st.dmin is None else st.dmin
        got = res.seps.get(id(ins))
        return None if got is None else got[1]

    def solve(self, att):
        tunables = [ins for ins in att.prog if ins.tune and ins.serves is not None]
        res = self.analyse(att)
        for safety in (1, 0):
            for _ in range(40):         # all shortfalls at once; each pass only lengthens delays
                moved = False
                for d in tunables:
                    kind, need = self.need(d.serves, safety)
                    have = self.have(res, d.serves)
                    if have is not None and have < need and d.a["d"] < isa.DLY_MAX:
                        d.a["d"] = min(isa.DLY_MAX, d.a["d"] + need - have)
                        moved = True
                if not moved:
                    break
                res = self.analyse(att)
            late = [d for d in tunables if d.serves.ob is not None and self.window_fails(res, d.serves)]
            if not late:
                break
            for d in late:                 # give back the safety tick where it costs the upper bound
                d.a["d"] = max(0, d.a["d"] - 1)
            res = self.analyse(att)
        return res

    def window_fails(self, res, ins):
        st = res.obs.get(id(ins))
        if st is None or st.dmin is None:
            return True
        lo, hi = self.achieved(ins.ob, st)
        return lo < ins.ob.lo or hi > ins.ob.hi

    # ------------------------------------------------------------ one role
    def compile(self):
        tried = []
        for mode in STRATEGIES:
            fsm = Fsm(self.sym, self.role, self.f, mode)
            prog, labels = layout(fsm.lower())
            if len(prog) > isa.MAX_INSTRUCTIONS:
                if mode == STRATEGIES[-1] and not tried:
                    raise TseError("R301", f"program too large: {len(prog)} instructions, a lane holds "
                                   f"{isa.MAX_INSTRUCTIONS} (8-bit jump targets)", self.role.name.span)
                continue
            att = FsmAttempt(mode, fsm, prog, labels)
            att.res = self.solve(att)
            att.violations = self.diagnostics(att)
            att.warnings = self.warnings(att)
            att.hint = HINTS[mode]
            tried.append(att)
            if not att.violations:
                return att, tried
            if not fsm_has_loops(prog):
                break
        return None, tried

    # ------------------------------------------------------------ diagnostics
    def diagnostics(self, att):
        out, res = [], att.res
        for ins in att.prog:
            if ins.ob is not None:
                st = res.obs.get(id(ins))
                if st is None:
                    out.append(self.unproven(ins, "never reached on any path, so it is unproven"))
                elif st.unanchored:
                    out.append(self.unproven(ins, st.unanchored))
                elif self.window_fails(res, ins):
                    out.append(self.window_error(ins, st))
            if ins.sep is not None:
                got = res.seps.get(id(ins))
                kind, need = self.need(ins, 0)
                if got is None:
                    out.append(self.unproven(ins, "its reference is not on every path"))
                elif self.sep_fails(ins, got):
                    out.append(self.sep_error(ins, got, need))
        for m in res.misses.values():
            out.append(self.miss_error(m))
        return sorted(out, key=lambda e: (e.span.line, e.span.col))

    def warnings(self, att):
        out = []
        for ins, wire, ev, latest, stable in stability_warnings(self.sym, self.role, att.res, self.f,
                                                                self.pads):
            span = ins.ob.span if ins.ob is not None else self.role.name.span
            out.append(TseError("W260", f"a guard reads '{wire}' up to {ticks_ns(latest, self.f)} after "
                                f"{printer.event(ev)}, but the datasheet keeps '{wire}' stable for "
                                f"only {ticks_ns(stable, self.f)} after it; add a hold rule such as "
                                f"{printer.event(ev)} -> edge({wire}) >= {ticks_ns(latest, self.f)}",
                                guard_span(att, ins, span)))
        return out

    def unproven(self, ins, why):
        ob = ins.ob
        code = "R311" if ob is not None else "R313"
        name = ob.name if ob is not None else ins.sep.text
        span = ob.span if ob is not None else ins.sep.span
        return TseError(code, f"obligation unproven in role '{self.role.name.text}': {name}: {why}", span)

    def window_error(self, ins, st):
        ob, f = ins.ob, self.f
        lo, hi = self.achieved(ob, st)
        late = hi > ob.hi
        what = "the pin changes" if ob.kind == "out" else "the sample is taken"
        fields = [
            ("obligation", f"OB-WIN '{ob.name} after {printer.event(ob.event)}' ({ob.source}), "
                           f"role {self.role.name.text}, lane {self.lane}"),
            ("window", f"[{ticks_ns(ob.lo, f)}, {ticks_ns(ob.hi, f)}] = [{math.ceil(ob.lo)}, "
                       f"{math.floor(ob.hi)}] ticks at {float(f) / 1e6:g} MHz{self.context(ob)}"),
            ("achieved", f"{ticks_ns(lo, f)} .. {ticks_ns(hi, f)}: {what} {float(lo):.2f} to "
                         f"{float(hi):.2f} ticks after the edge, over every slot phase and every "
                         "legal environment timing"),
            ("path", f"edge to synchronizer 1 to 2 ticks + program {st.dmin} to {st.dmax} cycles "
                     f"(least at {st.where_min}, most at {st.where_max})"
                     + (" + 2 output stages" if ob.kind == "out" else "")
                     + f", pads {self.pads_ns[0]:g} to {self.pads_ns[1]:g} ns"),
            ("late by" if late else "early by",
             ticks_ns(hi - ob.hi, f) if late else ticks_ns(ob.lo - lo, f)),
        ]
        fx = self.window_fix(ob, lo, hi, late)
        if fx:
            fields.append(("fix", fx))
        return TseError("R311", f"response window not met in role '{self.role.name.text}': "
                        f"{ob.name} {'late' if late else 'early'}", ob.span, fields=fields)

    def context(self, ob):
        names = sorted(set(fix.params_of(self.sym, ob.lo_expr)) | set(fix.params_of(self.sym, ob.hi_expr)))
        if not names:
            return ""
        vals = ", ".join(f"{n} = {fix.show(self.sym.params[n][1].v, self.sym.params[n][1].dim)}"
                         for n in names)
        return f"  ({vals})"

    def window_fix(self, ob, lo, hi, late):
        f = self.f
        parts = []
        if late:
            need = hi / f
            ideal = (hi - self.pads[1] + self.pads[0]) / f
            for p, text in fix.solve(self.sym, ob.hi_expr, need, at_least=True):
                ideal_txt = dict(fix.solve(self.sym, ob.hi_expr, ideal, at_least=True)).get(p)
                parts.append(text + (f" ({ideal_txt.split(' ', 2)[2]} with ideal pads)"
                                     if ideal_txt and self.pads[1] != self.pads[0] else ""))
            room = ob.hi - (hi - self.pads[1])
            if room >= self.pads[0] and self.pads[1] > room:
                parts.append(f"pads characterised at or below {ticks_ns(room, f)}")
        else:
            for p, text in fix.solve(self.sym, ob.lo_expr, lo / f, at_least=False):
                parts.append(text)
        return ",\nor ".join(parts) if parts else ""

    def sep_error(self, ins, got, need):
        sep = ins.sep
        lo, hi = self.sep_bounds(sep)
        if sep.hi is None:
            return TseError("R313", f"distance too short in role '{self.role.name.text}': {sep.text}",
                            sep.span, fields=[
                                ("obligation", f"OB-HOLD '{sep.text}' between two of our own outputs"),
                                ("required", f"at least {need} cycles"
                                 + (" (includes the pad skew between the pins)" if sep.skew else "")),
                                ("achieved", f"{got[1]} cycles at {got[2]}"),
                                ("fix", "a DLY beyond 511 cycles: use a shorter wait or the T counter")])
        late = got[3] > hi
        return TseError("R311", f"window after our own edge not met in role '{self.role.name.text}': "
                        f"{sep.text}", sep.span, fields=[
                            ("obligation", f"OB-WIN '{sep.text}', measured from our own earlier edge"),
                            ("window", f"[{ticks_ns(sep.ticks, self.f)}, {ticks_ns(sep.hi, self.f)}] at the "
                                       f"pins = {math.ceil(lo)} to {math.floor(hi)} cycles between the two "
                                       "instructions, pads and skew included"),
                            ("achieved", f"{got[1]} to {got[3]} cycles"),
                            ("late by" if late else "early by",
                             f"{got[3] - math.floor(hi)} cycles" if late
                             else f"{math.ceil(lo) - got[1]} cycles")])

    def miss_error(self, m):
        f = self.f
        ins = m.ins
        span = m.event.span if m.alt and m.event.span.line else self.wait_span(ins)
        ev = printer.event(m.event)
        prev = printer.event(m.prev) if m.prev is not None else "an unbounded wait"
        if m.alt:
            msg = (f"re-arm too late in role '{self.role.name.text}': the wait that detects {ev} "
                   f"(abort) starts {m.lateness} cycles after {ev} may already be visible")
            req = f"armed before the earliest {ev} after {prev}"
            rules = m.rules
            need_ticks = m.lateness + 1
            fx = self.rule_fix(rules, m.prev, m.event, need_ticks)
        else:
            opp = printer.event(_opposite(m.event))
            msg = (f"re-arm too late in role '{self.role.name.text}': the wait for {ev} starts "
                   f"{m.lateness} cycles after {ev} may be visible, and {opp} can follow "
                   f"{m.limit} cycles after it, so the edge can be missed")
            req = f"waiting for {ev} within {max(m.limit - 1, 0)} cycles of its earliest arrival"
            need_ticks = m.lateness + 1
            fx = self.rule_fix(m.rules, m.event, _opposite(m.event), need_ticks)
        fields = [("obligation", f"OB-REARM wait for {ev}, role {self.role.name.text}, lane {self.lane}"),
                  ("required", req),
                  ("achieved", f"dispatched up to {m.lateness} cycles late (worst at {m.where}), after "
                               f"{prev}")]
        if fx:
            fields.append(("fix", fx))
        return TseError("R314", msg, span, fields=fields)

    def rule_fix(self, rules, a, b, need_ticks):
        need = Fr(need_ticks) / self.f
        if not rules:
            return (f"add a datasheet rule {printer.event(a) if a is not None else 'E'} -> "
                    f"{printer.event(b)} >= {ticks_ns(Fr(need_ticks), self.f)} (nothing bounds it now), "
                    "or wait for it sooner")
        parts = []
        for r in rules:
            for p, text in fix.solve(self.sym, r.bound, need / len(rules), at_least=True):
                parts.append(text)
        cur = sum(self.sym.eval(r.bound).v for r in rules)
        parts.append(f"rule {', '.join(r.name.text for r in rules)} at least "
                     f"{ticks_ns(Fr(need_ticks), self.f)} (now {fix.show(cur, 1)})")
        return ",\nor ".join(parts)

    def wait_span(self, ins):
        return ins.wait.event.span if ins.wait is not None and ins.wait.event is not None \
            else self.role.name.span


def _opposite(e):
    from .semantics import opposite
    return opposite(e)


def guard_span(att, ins, default):
    return default if not getattr(ins, "span", None) else ins.span


def fsm_has_loops(prog):
    return any(ins.op == "JMP" and ins.a["cond"] in ("XDEC", "YDEC") for ins in prog)


HINTS = {"rolled": "counted loops kept rolled (a loop of more than two SRAM words refetches its "
                   "head every pass)",
         "unrolled": "counted loops unrolled (no loop overhead, more instructions)"}
