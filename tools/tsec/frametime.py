# SPDX-License-Identifier: Apache-2.0
"""Stage 5e, timing of frame roles: layout, delay solving, the proof of every obligation.

The search runs over (pc, slot phase, the fetch buffer, the flag JMP TOUT
tests, the age of every live mark) and asks cycles.step() for every
duration. A mark's age is an interval [least, most] cycles since its
cycle; an age past the largest constant that uses the mark is clamped
there and means "unbounded". At a WAIT for an engine event:
  - with no earliest visibility known on the path, the event may already be
    there and may have been there since any time (age unbounded);
  - with an earliest visibility e cycles from now (from a mark: TXNEED comes
    2 + 31 bit periods after a TXDATA write at the soonest), the WAIT ends at
    max(now, e) + j for every phase j, and its mark gets the visibility;
  - the alternate (host word waiting, end of frame) may end it at any phase;
  - a level test (timeout 0) takes either outcome; a host PULL or PUSH ends at
    once under `assume host.ready`, otherwise at any time, which makes every
    other mark unbounded.
Engine obligations that do not depend on the program (the response rule's
latency, the DPLL's sample points against the cell) are computed here too.
"""
import math
from dataclasses import dataclass, field
from fractions import Fraction as Fr

from . import assist_events as AE
from . import cycles, isa, printer
from .diag import TseError
from .framelower import FrameLowering, Ob
from .lir import Ins
from .pads import Pads

MAX_STATES = 1_500_000
PHASES = 16


# ------------------------------------------------------------------ layout
def layout(prog):
    """Labels resolved; a WAIT that reacts shares its word with the reaction, a WAIT followed by
    its JMP TOUT ends a word (its fall-through word is fetched while it waits), and an aligned
    label starts a word. Pads are DLY 0 with the side-set levels around them."""
    prog = drop_fallthrough(prog)
    out, labels, pending, align = [], {}, [], False
    for k, ins in enumerate(prog):
        if ins.op == "LABEL":
            pending.append(ins.label)
            align = align or getattr(ins, "align", False)
            continue
        want = None
        if align:
            want = 0
        elif ins.op == "WAIT" and getattr(ins, "odd", False):
            want = 1
        elif ins.op == "WAIT" and getattr(ins, "react", False):
            want = 0
        if want is not None and len(out) % 2 != want:
            pad = Ins("DLY", {"d": 0}, note="pad: word alignment")
            if "ss" in ins.a:
                prev = out[-1] if out and not pending else ins
                pad.a["ss"] = prev.a.get("ss", 0) if not getattr(ins, "mark", None) else \
                    (out[-1].a.get("ss", 0) if out else 0)
            out.append(pad)
        for name in pending:
            labels[name] = len(out)
        if pending:
            ins.label = next((n for n in pending if not n[-1].isdigit()), pending[0])
        pending, align = [], False
        out.append(ins)
    for name in pending:
        labels[name] = len(out)
    return out, labels


def drop_fallthrough(prog):
    out = []
    for k, ins in enumerate(prog):
        if ins.op == "JMP" and ins.a["cond"] == "ALWAYS" and ins.ob is None:
            j, names = k + 1, set()
            while j < len(prog) and prog[j].op == "LABEL":
                names.add(prog[j].label)
                j += 1
            if ins.a["target"] in names:
                continue
        out.append(ins)
    return out


# ------------------------------------------------------------------ analysis
@dataclass
class Stat:
    vmin: object = None
    vmax: object = None
    visits: int = 0
    unanchored: int = 0
    where: str = ""


class Analysis:
    def __init__(self, low, prog, labels, lane, fm):
        self.low, self.prog, self.labels, self.lane, self.fm = low, prog, labels, lane, fm
        self.host_ready = low.host_ready
        self.clamp = {}
        for ins in prog:
            ob = getattr(ins, "ob", None)
            if ob is not None:
                v = max(x for x in (ob.lo, ob.hi, ob.ideal, 0) if x is not None)
                self.need(ob.mark, v + abs(ob.offset))
            fw = getattr(ins, "fw", None)
            if fw is not None:
                for m, n in pairs(fw.earliest) + pairs(fw.alt_earliest):
                    self.need(m, n)
        self.stats = {}
        self.states = 0
        self.live = self.liveness()

    def liveness(self):
        """Per pc, the marks a later instruction may still read before they are set again."""
        def uses(ins):
            out = set()
            ob = getattr(ins, "ob", None)
            if ob is not None:
                out.add(ob.mark)
            fw = getattr(ins, "fw", None)
            if fw is not None:
                out |= {m for m, _ in pairs(fw.earliest) + pairs(fw.alt_earliest)}
            return out

        def sets(ins):
            fw = getattr(ins, "fw", None)
            out = {getattr(ins, "mark", None)}
            if fw is not None and not fw.alt:
                out.add(fw.mark)
            return out - {None}
        n = len(self.prog)
        live = [set() for _ in range(n + 1)]
        changed = True
        while changed:
            changed = False
            for pc in range(n - 1, -1, -1):
                ins = self.prog[pc]
                after = set()
                for s in self.successors(pc, None):
                    after |= live[s] if s <= n else set()
                v = uses(ins) | (after - sets(ins))
                if v != live[pc]:
                    live[pc], changed = v, True
        return live

    def need(self, mark, v):
        self.clamp[mark] = max(self.clamp.get(mark, 0), int(math.ceil(v)) + 2 * PHASES)

    def age(self, marks, d):
        out = {}
        for m, (a, b) in marks.items():
            cl = self.clamp.get(m)
            if cl is None:
                continue
            out[m] = (min(a + d, cl), min(b + d, cl))
        return out

    def unbound(self, marks, keep=()):
        return {m: (a, self.clamp[m] if m not in keep else b) for m, (a, b) in marks.items()}

    def successors(self, pc, flag):
        ins = self.prog[pc]
        if ins.op != "JMP":
            return [pc + 1]
        t = self.labels[ins.a["target"]]
        c = ins.a["cond"]
        if c == "ALWAYS":
            return [t]
        if c == "TOUT" and flag is not None:
            return [t] if flag in ("alt", "tmo") else [pc + 1]
        return [pc + 1, t]

    def rel(self, marks, spec):
        """Cycles from now to the earliest visibility (None: may have been there for ever)."""
        for m, n in pairs(spec):
            if m in marks:
                return n - marks[m][1]
        return None

    def outcomes(self, ins, c, marks):
        """[(end cycle, flag, mark name, (least, most) age of the event at its end, unbounded)]."""
        fw = getattr(ins, "fw", None)
        if ins.op not in ("WAIT", "PULL", "PUSH") or (ins.op != "WAIT" and not ins.a.get("blk")):
            return [(None, None, None, None, False)]
        if fw is None:
            raise TseError("R300", f"internal: a wait without its event at {printer_ins(ins)}")
        if fw.key == "HOST":
            if self.host_ready:
                return [(c, None, None, None, False)]
            return [(c + j, None, None, None, True) for j in range(PHASES)]
        if fw.test:
            return [(c, "own", None, None, False), (c, "tmo", None, None, False)]
        out = []
        if fw.pulse:
            out += [(c + j, "own", fw.mark, (0, 0), True) for j in range(PHASES)]
        else:
            r = self.rel(marks, fw.earliest)
            if r is None or r <= 0:
                most = None if r is None else -r
                out.append((c, "own", fw.mark, (0, most), False))
                out += [(c + j, "own", fw.mark, (0, 0), True) for j in range(1, PHASES)]
            else:
                out += [(c + r + j, "own", fw.mark, (0, 0), True) for j in range(PHASES)]
        if fw.alt is not None:
            r = 0 if fw.alt == "HOST" else self.rel(marks, fw.alt_earliest)
            if r is None or r <= 0:
                most = None if r is None else -r
                out.append((c, "alt", fw.alt_mark, (0, most), False))
                out += [(c + j, "alt", fw.alt_mark, (0, 0), True) for j in range(1, PHASES)]
            else:
                out += [(c + r + j, "alt", fw.alt_mark, (0, 0), True) for j in range(PHASES)]
        return out

    def check(self, ins, c, marks):
        ob = getattr(ins, "ob", None)
        if ob is None:
            return
        st = self.stats.setdefault(id(ins), Stat())
        if ob.mark not in marks:
            st.unanchored += 1
            return
        a, b = marks[ob.mark]
        cl = self.clamp[ob.mark]
        lo, hi = a + ob.offset, (b + ob.offset if b < cl else None)
        st.visits += 1
        if st.vmin is None or lo < st.vmin:
            st.vmin = lo
        if hi is None or st.vmax is None or (st.vmax is not False and hi > st.vmax):
            st.vmax = hi if hi is not None else False       # False: unbounded
            st.where = f"slot phase {c % 4}"

    def run(self):
        start = cycles.start_cycle(self.lane, self.fm)
        init = (0, start, (0,), (), None)
        seen, work = set(), [init]
        while work:
            pc, c, tags, mk, flag = work.pop()
            key = (pc, c % isa.DATA_SLOT_PERIOD, tags, mk, flag)
            if key in seen:
                continue
            seen.add(key)
            if len(seen) > MAX_STATES:
                raise TseError("R325", f"timing analysis of role '{self.low.role.name.text}' exceeds "
                               f"{MAX_STATES} states", self.low.role.name.span)
            if pc >= len(self.prog):
                continue
            ins = self.prog[pc]
            marks = dict((m, (a, b)) for m, a, b in mk)
            self.check(ins, c, marks)
            if getattr(ins, "mark", None) and ins.mark in self.clamp:
                marks[ins.mark] = (0, 0)
            for end, fl, setm, ev_age, unbounded in self.outcomes(ins, c, marks):
                nflag = fl if ins.op == "WAIT" else flag        # AFLAG, TFLAG clear at a WAIT only
                for npc in self.successors(pc, flag):
                    buf = list(tags)
                    c2 = cycles.step(ins.op, ins.a, c, pc, npc, buf, self.lane, self.fm, end)
                    m2 = self.age(marks, c2 - c)
                    if unbounded:
                        m2 = self.unbound(m2, keep=())
                    if setm is not None and setm in self.clamp:
                        lo = c2 - end + ev_age[0]
                        hi = self.clamp[setm] if ev_age[1] is None else c2 - end + ev_age[1]
                        m2[setm] = (min(lo, self.clamp[setm]), min(hi, self.clamp[setm]))
                    keep = self.live[npc] if npc < len(self.prog) else ()
                    mk2 = tuple(sorted((m, a, b) for m, (a, b) in m2.items() if m in keep))
                    work.append((npc, c2, tuple(buf), mk2, nflag))
        self.states = len(seen)
        return self


def pairs(spec):
    if spec is None:
        return []
    if isinstance(spec[0], str):
        return [spec]
    return list(spec)


def printer_ins(ins):
    return isa.fmt(ins.op, {k: v for k, v in ins.a.items() if k != "expect_taken"})


# ------------------------------------------------------------------ result
@dataclass
class Attempt:
    mode: str
    low: object
    prog: list
    labels: dict
    an: object = None
    rows: list = field(default_factory=list)
    violations: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    static: list = field(default_factory=list)


@dataclass
class FrameResult:
    role: object
    lane: int
    chosen: object = None
    tried: list = field(default_factory=list)
    kind: str = "frames"


def compile_role(sym, role, lane, fclk, fm, ppm, pads):
    """Lower, lay out, solve the placement and hold delays, prove; R1 on every obligation."""
    fclk = Fr(fclk)
    low = FrameLowering(sym, role, fclk, lane).lower()
    prog, labels = layout(low.prog)
    low.ir, low.prog = low.prog, prog       # code generation reads the laid-out program
    if len(prog) > isa.MAX_INSTRUCTIONS:
        raise TseError("R301", f"program too large: {len(prog)} instructions, a lane holds "
                       f"{isa.MAX_INSTRUCTIONS}", role.name.span)
    att = Attempt("frames", low, prog, labels)
    att.warnings = list(low.warnings)
    # the engine's rule drive answers a field start the receiver found on an input: one pad in
    # the hull of the input and output ranges, as for any output after an input edge (pads.py)
    pmin, pmax = Pads.of(pads).ticks_by_kind(fclk)["out"]
    solve(att, lane, fm, ppm)
    att.rows, att.violations = judge(att, fclk, ppm)
    att.static, bad = static_obs(sym, low, fclk, ppm, pmin, pmax)
    att.violations += bad
    res = FrameResult(role, lane, tried=[att])
    if not att.violations:
        res.chosen = att
    return res


def solve(att, lane, fm, ppm):
    tun = [i for i in att.prog if i.tune and i.serves is not None]
    for _ in range(12):
        att.an = Analysis(att.low, att.prog, att.labels, lane, fm).run()
        moved = False
        for d in tun:
            ob = d.serves
            target = next((i for i in att.prog if getattr(i, "ob", None) is ob), None)
            st = att.an.stats.get(id(target))
            if st is None or st.vmin is None or st.vmax in (None, False):
                continue
            if ob.kind == "place":
                shift = round(ob.ideal - Fr(st.vmin + st.vmax, 2))
            else:
                shift = max(0, math.ceil(ob.lo - st.vmin))
            nd = min(isa.DLY_MAX, max(0, d.a["d"] + shift))
            if nd != d.a["d"]:
                d.a["d"], moved = nd, True
        if not moved:
            break


def ticks(v, fclk):
    return f"{float(v / fclk * 10**9):.1f} ns"


def judge(att, fclk, ppm):
    rows, bad = [], []
    for ins in att.prog:
        ob = getattr(ins, "ob", None)
        if ob is None:
            continue
        st = att.an.stats.get(id(ins), Stat())
        if not st.visits:
            why = "never reached with its anchor on the analysed paths, so it is unproven" \
                if st.unanchored == 0 else f"reached only without its anchor ({ob.mark})"
            if ob.kind == "rearm" and st.unanchored:
                rows.append((ob, "first entry only", "ok"))
                continue
            bad.append(TseError(ob.code, f"{ob.name}: {why}", ob.span))
            continue
        lo, hi = st.vmin, st.vmax
        unb = hi is False
        shown = f"{lo} to {'unbounded' if unb else hi} cycles"
        if ob.kind in ("feed", "rearm"):
            ok = not unb and hi <= ob.hi and (ob.lo is None or lo >= ob.lo)
            need = f"[{'' if ob.lo is None else ob.lo}, {ob.hi}] cycles"
        elif ob.kind == "place":
            budget = ob.tol - Fr(ppm, 10**6) * ob.ideal
            err = None if unb else max(abs(lo - ob.ideal), abs(hi - ob.ideal))
            ok = err is not None and err <= budget
            need = f"{float(ob.ideal):.2f} +/- {float(budget):.2f} ticks"
            shown += "" if err is None else f", error {float(err):.2f}"
        else:
            ok = lo >= ob.lo
            need = f">= {float(ob.lo):.2f} ticks"
        rows.append((ob, f"{shown} (needs {need})", "ok" if ok else "FAIL"))
        if not ok:
            bad.append(rejection(ob, lo, hi, att, fclk, ppm))
    return rows, bad


TITLES = {"R316": "assist feed late", "R310": "output placement outside tolerance",
          "R313": "hold too short", "R314": "re-arm too late", "R311": "response window not met",
          "R312": "sample placement outside tolerance"}


def rejection(ob, lo, hi, att, fclk, ppm):
    role = att.low.role.name.text
    unb = hi is False
    fields = [("obligation", f"{ob.name}, role {role}, lane {att.low.lane}"),
              ("why", ob.why)]
    host = not att.low.host_ready and any(getattr(getattr(i, "fw", None), "key", None) == "HOST"
                                          for i in att.prog)
    if ob.kind in ("feed", "rearm"):
        fields.append(("window", f"at most {ob.hi} cycles after {ob.mark}" if ob.lo is None else
                       f"{ob.lo} to {ob.hi} cycles after {ob.mark}"))
        fields.append(("achieved", f"{lo} to {'unbounded' if unb else hi} cycles, over every slot "
                       "phase, buffer state and arrival time"))
        if unb:
            fields.append(("late by", "unbounded: the path waits for the host or for an event with no "
                           "bound" + (" (the role has no `assume host_ready`)" if host else "")))
        else:
            fields.append(("late by", f"{hi - ob.hi} cycles ({ticks(hi - ob.hi, fclk)})"))
        if ob.kind == "rearm":
            need = None if unb else hi + 1 + math.ceil(ob.delay)
            ev = printer.event(ob.end)
            fields.append(("fix", f"a datasheet rule bounding {ev} to the next start of frame at "
                           f"{need} ticks or more ({ticks(need, fclk)})" if need is not None else
                           "add `assume host_ready : host.ready;` (the host takes each word at once) "
                           f"and a datasheet rule bounding {ev} to the next start of frame"))
        elif unb and host:
            fields.append(("fix", "add `assume host_ready : host.ready;` to the role"))
    elif ob.kind == "place":
        budget = ob.tol - Fr(ppm, 10**6) * ob.ideal
        fields.append(("window", f"{float(ob.ideal):.2f} +/- {float(budget):.2f} ticks after the "
                       "earlier instant (tolerance less the clock budget)"))
        fields.append(("achieved", f"{lo} to {'unbounded' if unb else hi} ticks"))
    else:
        fields.append(("window", f"at least {float(ob.lo):.2f} ticks"))
        fields.append(("achieved", f"{lo} ticks at the least"))
    return TseError(ob.code, f"{TITLES.get(ob.code, 'obligation unproven')} in role '{role}': "
                    f"{ob.name}", ob.span, fields=fields)


# ------------------------------------------------------------------ engine obligations
def strobe_window(q16, thr, dual):
    """(least, most) cycles from the true edge of a hard sync to the pin sample a DPLL strobe with
    threshold thr (1/16 cycle) takes, for bit 0, and the per-bit phase pattern of later bits."""
    e = Fr(1, 4) if dual else Fr(1, 2)
    out = []
    for lag in ((4, 12) if dual else (8,)):
        for k in range(16):
            n = math.ceil(max(0, thr + k * q16 - 16 - lag) / 16)
            est = Fr(16 + lag + 16 * n, 16) - Fr(k * q16, 16)
            out += [est - e, est + e]
    return min(out), max(out)


def static_obs(sym, low, fclk, ppm, pmin, pmax):
    """Rows and rejections for what the engines do without the program."""
    rows, bad = [], []
    aw = getattr(low, "ack_window", None)
    if aw is not None:
        e, lo, hi, f = aw
        got = (2 + pmin, 2 + pmax)          # bit start in cycle b: the drive reaches the pin at b + 2
        ok = got[0] >= lo and got[1] <= hi
        rows.append((f"respond {e.name.text}: rule 0 drives field '{f.name}' {float(got[0]):.2f} to "
                     f"{float(got[1]):.2f} ticks after the field starts (pads included), window "
                     f"[{float(lo):.2f}, {float(hi):.2f}]", "ok" if ok else "FAIL"))
        if not ok:
            bad.append(TseError("R311", f"response window not met in role '{low.role.name.text}': "
                                f"respond {e.name.text}", e.span,
                                fields=[("achieved", f"{float(got[0]):.2f} to {float(got[1]):.2f} ticks"),
                                        ("window", f"[{float(lo):.2f}, {float(hi):.2f}] ticks")]))
    d = low.assists.get("dpll")
    if d is not None and low.rx_pin is not None:
        rows_s, bad_s = sampling(sym, low, fclk, ppm, d)
        rows += rows_s
        bad += bad_s
    if low.period is not None and low.codec_pin == low.tx_pin and low.tx_pin is not None:
        err = Fr(low.q16, 16) - low.period
        rows.append((f"bit timer: {low.q16}/16 = {low.q16 / 16:.4f} ticks per bit "
                     f"for {float(low.period):.4f} "
                     f"({float(err):+.4f}); bits and half bits land on whole cycles"
                     + (" (period from unit 'bit', no dpll assist declared)" if low.dpll_from_unit else ""),
                     "info"))
    return rows, bad


def jitter(sym, low, half):
    """Per-edge jitter from a datasheet rule on the edges of the receive wire: the rule's bound
    below the nominal least spacing."""
    from . import ast as A
    w = low.rx_pin
    best = Fr(0)
    for ds in sym.spec.of(A.Datasheet):
        for r in ds.rules:
            if r.relop in (">=", ">") and r.a.name.text == w and r.b.name.text == w:
                nominal = low.period / 2 if half else low.period
                best = max(best, nominal - sym.eval(r.bound).v * low.f)
    return best


def sampling(sym, low, fclk, ppm, d):
    from .assists import Params
    p = Params(sym, d, fclk)
    dual = p.flag("dual_edge")
    q16, T = low.q16, low.period
    man = "manchester" in low.assists
    spt = max(0, round(T * low.sample * 16) - 8)
    low.spt_reg = spt
    J = jitter(sym, low, man)
    lay_bits = max(st.frame.total()[1] for st in low.status_for.values()) if low.status_for else 0
    rows, bad = [], []
    if man and low.sjw:
        bad.append(TseError("R312", f"sample placement in role '{low.role.name.text}': a DPLL resync "
                            "(sjw) with a Manchester line takes the middle transitions for bit "
                            "boundaries", d.name.span, fields=[("fix", "sjw = 0")]))
        return rows, bad
    if low.sjw and not man:
        stuff = low.assists.get("stuffer")
        run = Params(sym, stuff).number("run", lo=1, hi=15) if stuff else None
        span_bits = 2 * run if run else lay_bits
        what = f"between resync edges ({span_bits} bits at most with stuffing)"
    else:
        span_bits = lay_bits
        what = f"over the longest frame ({span_bits} bits, no resync)"
    drift_bit = abs(Fr(q16, 16) - T) + T * Fr(ppm, 10**6)
    strobes = [("sample", spt, (T / 2 + J, T - J) if man else (J, T - J))]
    if man:
        strobes.insert(0, ("half-bit check", q16 >> 2, (J, T / 2 - J)))
    for name, thr, (rlo, rhi) in strobes:
        a, b = strobe_window(q16, thr, dual)
        margin = min(a - rlo, rhi - b)
        drift = drift_bit * span_bits
        if low.sjw and not man and drift > low.sjw:
            margin = Fr(-1)
        ok = margin - drift >= 0
        bits_ok = int(margin / drift_bit) if drift_bit and margin >= 0 else (0 if margin < 0 else span_bits)
        rows.append((f"DPLL {name}: {float(a):.2f} to {float(b):.2f} ticks into the cell at bit 0, must stay "
                     f"in [{float(rlo):.2f}, {float(rhi):.2f}]"
                     + (f" ({ticks(J, fclk)} jitter)" if J else "")
                     + f"; margin {float(margin):.2f}, drift {float(drift):.2f} {what}",
                     "ok" if ok else "FAIL"))
        if not ok:
            fix = []
            if margin < 0:
                fix.append("the strobe misses at bit 0 already: " + (
                    "dual_edge = false, or less jitter" if dual else "less jitter"))
            else:
                fix.append(f"frames of at most {bits_ok} bits at {ppm} ppm, or a clock budget of "
                           f"{int(margin / (T * span_bits) * 10**6) if span_bits else 0} ppm or less")
            bad.append(TseError("R312", f"sample placement outside tolerance in role "
                                f"'{low.role.name.text}': DPLL {name} on {low.rx_pin}", d.name.span,
                                fields=[("obligation", f"OB-SAMP, the {name} strobe reads the right "
                                         "part of every cell"),
                                        ("achieved", f"{float(a):.2f} to {float(b):.2f} ticks into the "
                                         f"cell at bit 0, plus {float(drift_bit):.5f} ticks per bit"),
                                        ("window", f"[{float(rlo):.2f}, {float(rhi):.2f}] ticks"),
                                        ("late by", f"{float(drift - margin):.2f} ticks at bit "
                                         f"{span_bits}" if margin >= 0 else
                                         f"{float(-margin):.2f} ticks at bit 0"),
                                        ("fix", "; ".join(fix))]))
    return rows, bad


# ------------------------------------------------------------------ report
def section(res, rows, fclk, pads):
    from .codegen import listing_lines
    role = res.role
    att = res.chosen or res.tried[0]
    out = ["", f"== role {role.name.text} ({role.kind}) on lane {res.lane}"]
    if res.chosen is None:
        out.append("REJECTED: an obligation is not met (rule R1, all or nothing)")
    else:
        out.append(f"strategy: frame engine ({att.an.states} analysis states)")
        out += listing_lines(rows)
    out.append("obligations (cycles unless stated; worst case over every slot phase, buffer state and "
               "event arrival):")
    for ob, text, verdict in att.rows:
        out.append(f"  line {ob.span.line:2d} {ob.kind:<5} {ob.name}: {text}  {verdict}")
    for text, verdict in att.static:
        out.append(f"  engine {text}  {verdict}")
    for k, v in sorted(att.low.notes.items()):
        out.append(f"  host contract, {k}: {v}")
    if att.low.host_ready:
        out.append("  assumption host_ready: every host PULL and PUSH ends at once (labelled)")
    if res.chosen is not None:
        out.append(f"ACCEPTED: {len(rows)} instructions, image "
                   + " ".join(f"{w:04x}" for _, w, _, _, _ in rows))
    return out


def diags(res):
    return res.tried[0].violations
