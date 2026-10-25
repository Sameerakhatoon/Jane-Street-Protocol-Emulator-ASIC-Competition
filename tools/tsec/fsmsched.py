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

from . import fix, isa, pace, printer
from .diag import TseError
from .fsm import STRATEGIES, Fsm, NeedHome, layout
from . import ast as A
from .fsmtime import Analysis, stability_warnings
from .lower import unsupported
from .units import drift_budget
from .window import DATA_WORDS, WindowPlan


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
    def __init__(self, sym, role, lane, fclk, fm, pads, ppm=50, window=(0, DATA_WORDS)):
        self.sym, self.role, self.lane, self.f, self.fm, self.ppm = sym, role, lane, fclk, fm, ppm
        self.wbase, self.wlimit = window
        self.pads = (Fr(pads[0]) * fclk / 10**9, Fr(pads[1]) * fclk / 10**9)   # ns to ticks
        self.skew = Fr(pads[2] if len(pads) > 2 else pads[1] - pads[0]) * fclk / 10**9
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
            skew = self.skew if sep.skew else 0
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

    def lay(self, att):
        att.prog, att.labels = layout(att.ir)
        f = att.fsm
        pace.apply_side(att.prog, att.labels, f.side, f.pin_out, f.side_levels(), self.role.name.span)

    def solve_paces(self, att):
        """Set each pacing delay so that every action of a chain issues at its ideal instant
        rounded to a cycle: the delay after an action sets the distance to the action issued next
        (its first visit decides; later visits of a rolled loop are checked, not solved). Pacing
        delays that are not needed leave the program."""
        if not att.fsm.chain_list:
            return
        for _ in range(400):
            res = self.analyse(att)
            moved = False
            for chain in att.fsm.chain_list:
                rec = res.paces.get(id(chain), {})
                e0 = chain.entries[0]
                order = sorted(rec.items(), key=lambda kv: (kv[1][0], kv[0]))     # issue order
                seen = set()
                for (ka, ra), (kb, rb) in zip(order, order[1:]):
                    a = ra[2]
                    ctl = getattr(a, "pace_dly", None)
                    fld = "delay" if ctl is a and a.pace_field == "delay" else "d"
                    if a is rb[2] or ctl is None or ctl.a.get(fld, 0) < 0 or id(ctl) in seen:
                        continue
                    seen.add(id(ctl))
                    want = self.target(chain, kb, e0) - self.target(chain, ka, e0)
                    top = 7 if fld == "delay" else isa.DLY_MAX
                    new = max(0, min(top, ctl.a.get(fld, 0) + want - (rb[0] - ra[0])))
                    if new != ctl.a.get(fld, 0):    # one delay per pass, earliest first: a change
                        ctl.a[fld], moved = new, True       # moves every later fetch
                        break
                if moved:
                    break
            if not moved:
                return

    def target(self, chain, k, e0):
        """Issue cycle of entry k after entry 0's issue that puts it nearest its ideal."""
        e = chain.entries[k]
        o = (pace.OUT_OFFSET if e.kind == "out" else 0) - (pace.OUT_OFFSET if e0.kind == "out" else 0)
        return math.floor(e.ideal - o + Fr(1, 2))

    def pace_rows(self, att):
        """(stmt, entry, error ticks, budget ticks, where, ok) per timed statement of each chain:
        the worst entry over every visit, slot phase and buffer state."""
        rows = []
        for chain in att.fsm.chain_list:
            rec = att.res.paces.get(id(chain), {})
            e0 = chain.entries[0]
            worst = {}
            for k, e in enumerate(chain.entries):
                st = e.stmts[0]
                r = rec.get(k)
                if r is None:
                    worst.setdefault(id(st), (st, k, None, None, "never reached on any path", False, 0))
                    continue
                o = (pace.OUT_OFFSET if e.kind == "out" else 0) - \
                    (pace.OUT_OFFSET if e0.kind == "out" else 0)
                skew = self.skew if (set(e.wires) != set(e0.wires) or e.kind != e0.kind) else 0
                err = max(abs(r[0] + o - e.ideal), abs(r[1] + o - e.ideal)) + skew
                budget = drift_budget(e.tol, e.ideal, self.ppm)
                where = r[3] if abs(r[0] + o - e.ideal) >= abs(r[1] + o - e.ideal) else r[4]
                row = (st, k, err, budget, where, err <= budget, skew)
                old = worst.get(id(st))
                if old is None or (old[2] is not None and err - budget > old[2] - old[3]):
                    worst[id(st)] = row
            rows += worst.values()
        return rows

    def solve(self, att):
        self.solve_paces(att)
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

    def rule_window(self, ob):
        """(tick_delay, lo, hi): the rule's pin change after the edge, [e + td + E] with e two
        cycles after the pin edge, so (2 + td + E - 1, 2 + td + E] ticks, plus the pads; td is the
        least delay that keeps the window's lower bound."""
        pmin, pmax = self.pads
        base = 1 + isa.EDGE_TO_PORT
        td = max(0, math.ceil(ob.lo - pmin - base))
        return td, base + td + pmin, base + td + 1 + pmax

    def window_fails(self, res, ins):
        st = res.obs.get(id(ins))
        if st is None or st.dmin is None:
            return True
        lo, hi = self.achieved(ins.ob, st)
        return lo < ins.ob.lo or hi > ins.ob.hi

    # ------------------------------------------------------------ one role
    def compile(self):
        att, tried = self.attempts(None)
        if att is None and self.rule_candidate() is not None:
            att, more = self.attempts(self.rule_candidate())
            tried += more
        return att, tried

    def rule_candidate(self):
        """A respond on a pin edge whose body shifts one bit out: response rule 0 in pin-edge mode
        with hold drives that bit (needs RULE_CLK.hold in the register table)."""
        if not isa.RULE_HOLD:
            return None
        for st in self.role.states:
            for e in st.edges:
                if isinstance(e, A.RespondEdge) and e.guard is None and e.event.kind in ("rise", "fall") \
                        and len(e.body) == 1 and isinstance(e.body[0], A.Shift) and e.body[0].out \
                        and len(e.body[0].wires) == 1 and int(self.sym.eval(e.body[0].count).v) == 1:
                    return RuleFirst(e, e.body[0].var.text, e.body[0].wires[0].text, e.event)
        return None

    def attempts(self, rule):
        tried = []
        for mode in STRATEGIES:
            fsm, ir = self.lower(mode, rule)
            att = FsmAttempt(mode, fsm, [], {})
            att.ir = ir
            self.lay(att)
            prog = att.prog
            if len(prog) > isa.MAX_INSTRUCTIONS:
                if mode == STRATEGIES[-1] and not tried:
                    raise TseError("R301", f"program too large: {len(prog)} instructions, a lane holds "
                                   f"{isa.MAX_INSTRUCTIONS} (8-bit jump targets)", self.role.name.span)
                continue
            att.res = self.solve(att)
            att.violations = self.diagnostics(att)
            att.pace_rows = self.pace_rows(att)
            att.warnings = self.warnings(att)
            att.hint = HINTS[mode]
            tried.append(att)
            if not att.violations:
                return att, tried
            if not fsm_has_loops(prog):
                break
        return None, tried

    def lower(self, mode, rule=None):
        """Lower the role; each NeedHome adds the variable to the ones kept in the data window."""
        persist = []
        while True:
            fsm = Fsm(self.sym, self.role, self.f, mode, persist,
                      WindowPlan(self.role, self.wbase, self.wlimit), rule)
            try:
                return fsm, fsm.lower()
            except NeedHome as e:
                if e.var in persist:
                    raise unsupported(f"'{e.var}' is read where no copy of it is current",
                                      self.role.name.span) from None
                persist.append(e.var)

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
        for ins in att.prog:
            rob = getattr(ins, "rule_ob", None)
            if rob is not None:
                td, lo, hi = self.rule_window(rob)
                att.fsm.rule.td, att.fsm.rule.lo, att.fsm.rule.hi = td, lo, hi
                if lo < rob.lo or hi > rob.hi or td > 255:
                    out.append(TseError("R311", f"response window not met in role '{self.role.name.text}': "
                                        f"{rob.name} by response rule late", rob.span, fields=[
                                            ("window", f"[{ticks_ns(rob.lo, self.f)}, "
                                                       f"{ticks_ns(rob.hi, self.f)}]"),
                                            ("achieved", f"{ticks_ns(lo, self.f)} .. {ticks_ns(hi, self.f)} "
                                                         f"with tick_delay {td}"),
                                            ("late by", ticks_ns(hi - rob.hi, self.f))]))
        for ins in att.prog:                    # a wait that bounds nothing would be unproven
            if ins.tune and ins.serves is None:
                out.append(TseError("R313", f"obligation unproven in role '{self.role.name.text}': "
                                    f"{ins.note}: no later action carries its distance",
                                    self.role.name.span))
        for m in res.misses.values():
            out.append(self.miss_error(m))
        for ins, why in res.pace_bad.values():
            st = ins.paces[-1].stmt
            out.append(TseError("R310", f"obligation unproven in role '{self.role.name.text}': "
                                f"{printer.stmt(st, 0).rstrip(';')}: {why}", st.span))
        for st, k, err, budget, where, ok, skew in self.pace_rows(att):
            if not ok:
                out.append(self.pace_error(st, k, err, budget, where, skew))
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

    def pace_error(self, st, k, err, budget, where, skew):
        out = pace.is_out(st)
        code = "R310" if out else "R312"
        title = "output placement outside tolerance" if out else "sample placement outside tolerance"
        text = printer.stmt(st, 0).rstrip(";")
        if err is None:
            return TseError(code, f"obligation unproven in role '{self.role.name.text}': {text}: {where}",
                            st.span)
        return TseError(code, f"{title} in role '{self.role.name.text}': {text}", st.span, fields=[
            ("obligation", f"{'OB-EDGE' if out else 'OB-SAMP'} '{text}', timeline entry {k} of its "
                           f"sequence, role {self.role.name.text}, lane {self.lane}"),
            ("achieved", f"error up to {float(err):.2f} ticks ({ticks_ns(err, self.f)}) from the ideal "
                         f"instant, worst at {where}, pad skew between different pins included "
                         f"({ticks_ns(self.skew, self.f)})"),
            ("budget", f"{float(budget):.2f} ticks ({ticks_ns(budget, self.f)}) after {self.ppm} ppm "
                       "of drift"),
            ("fix", f"pads characterised with a skew at or below {ticks_ns(budget - err + skew, self.f)} "
                    "between these pins (--pad-skew)" if skew and budget - err + skew >= 0 else
             "a wider tolerance; a rolled loop repeats its delays, so a period that is not a whole "
             "number of cycles drifts, and unrolled code places every action")])

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


class RuleFirst:
    """Response rule 0 answers a respond: clocked by the respond's pin edge, any position, its
    one bit from the word the program loads, kept (hold) until the program writes the pin."""

    def __init__(self, edge, var, wire, event):
        self.edge, self.var, self.wire, self.event = edge, var, wire, event


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
