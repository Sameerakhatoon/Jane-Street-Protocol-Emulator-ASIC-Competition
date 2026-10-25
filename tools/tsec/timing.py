"""Stage 5b, timing: the phase-exhaustive analysis of a paced frame.

The analysis runs the expected path of a lowered frame from its anchor for
every slot phase and for a cold and a warm buffer, records when each
obligation's instruction issues, and checks it against the semantics
(semantics.py). Instruction cycles come only from cycles.py. Nothing here
reads the spec.
"""
import math
from dataclasses import dataclass, field
from fractions import Fraction as Fr

from . import cycles, isa
from .cycles import FetchModel  # noqa: F401 (re-export)
from .diag import TseError
from .semantics import hold_ok, rearm_ok
from .units import drift_budget


class Overrun(Exception):
    pass


@dataclass
class Violation:
    code: str
    text: str           # the report line
    span: object
    detail: str = ""    # the same without the name and line, for diagnostics


@dataclass
class Row:
    ob: object
    visit: int
    cost: Fr
    shown: tuple
    budget: Fr
    ok: bool


@dataclass
class Report:
    rows: list
    violations: list
    cost: tuple
    hold_lo: int = None
    hold_hi: int = None
    unvisited: list = field(default_factory=list)


def simulate(low, labels, lane, fm, t0, buf0, nco_phase=Fr(0)):
    """Run the expected path from the anchor (dispatched at t0, buffer buf0) until the anchor
    dispatches again. Returns ([(obligation, visit, issue)], presentation cycle, buffer)."""
    prog = low.prog
    visits = {}
    a0 = labels["idle"]           # the anchor, after the output prologue
    pc, t, x, first, buf = a0, t0, 0, True, list(buf0)
    nco_c, tick, out = None, 0, []
    for _ in range(100000):
        ins = prog[pc]
        if pc == a0 and not first:
            return out, t, buf
        t_ret, npc = t, pc + 1
        if ins.op == "WAIT" and ins.a["src"] == "EV":
            tick += 1
            tk = nco_c + math.floor(tick * low.nco_period + nco_phase)
            if tk < t:
                raise Overrun(f"instruction {pc} reached the timer wait at cycle {t - nco_c}, "
                              f"after tick {tick} at cycle {tk - nco_c}")
            t_ret = tk
        if ins.ob is not None:
            v = visits.get(id(ins.ob), 0)
            out.append((ins.ob, v, t_ret))
            visits[id(ins.ob)] = v + 1
        end = t_ret if ins.op == "WAIT" else None
        if ins.op == "SET" and ins.a["dst"] == "X":
            x = ins.a["imm"]
        elif ins.op == "SET" and ins.a["dst"] == "CMD" and ins.a["imm"] == isa.CMD_DPLL_ON:
            nco_c, tick = t_ret, 0
        elif ins.op == "JMP":
            c = ins.a["cond"]
            taken = (c == "ALWAYS" or (c == "XDEC" and x != 0) or
                     (c == "PIN" and ins.a.get("expect_taken", False)))
            if c == "XDEC":
                x = (x - 1) & 0xFFFF
            if taken:
                npc = labels[ins.a["target"]]
        pc, t, first = npc, cycles.step(ins.op, ins.a, t, pc, npc, buf, lane, fm, end), False
    raise TseError("R399", "expected path did not return to the anchor")


def runs_for_phase(low, labels, lane, fm, p, nco_phase):
    """Cold buffer (only the anchor's word, as after RUN) and the steady-state warm buffer."""
    tr, present, buf = simulate(low, labels, lane, fm, p, [labels["idle"] // 2], nco_phase)
    out = [("cold", tr, present)]
    for _ in range(4):
        tr2, present2, buf2 = simulate(low, labels, lane, fm, p, buf, nco_phase)
        if buf2 == buf:
            break
        buf = buf2
    out.append(("warm", tr2, present2))
    return out


def analyse(low, labels, lane, fm, ppm, nco_phase=Fr(0)):
    """Check every obligation of the frame for every slot phase and buffer state."""
    f = low.frame
    worst, viol = {}, []
    hold_lo, hold_hi = None, None
    for p in range(fm.S):
        try:
            cases = runs_for_phase(low, labels, lane, fm, p, nco_phase)
        except Overrun as e:
            return Report([], [Violation("R317", f"timer pacing overrun at phase {p}: {e}",
                                         f.actions[0].span)], (float(10**9), 1, 0.0))
        for bstate, trace, present in cases:
            where = f"phase {p}, {bstate} buffer"
            first_eff = None
            for ob, v, issue in trace:
                eff = issue + 1 if ob.kind == "out" else issue
                if first_eff is None:
                    first_eff = eff
                if ob.ref == "anchor":
                    rng = (Fr(issue - p), Fr(issue - p) + 1)      # S6: sample in [d, d + 1)
                    lo, hi = ob.window
                    pmin, pmax = getattr(low, "pads", (0, 0))   # pad delays, in ticks
                    err = max(lo - rng[0] - pmin, rng[1] + pmax - hi, 0)
                    cost = abs(rng[0] + Fr(1, 2) - ob.ideals[v])
                    ok, shown, budget = err == 0, rng, (hi - lo) / 2
                else:
                    ideal = ob.ideals[v] - trace[0][0].ideals[0]
                    rel = eff - first_eff
                    budget = drift_budget(ob.tol, ideal, ppm)
                    cost = abs(rel - ideal)
                    ok, shown = cost <= budget, (Fr(rel), Fr(rel))
                key = (id(ob), v)
                w = worst.get(key)
                if w is None or cost > w[0].cost or not ok:
                    worst[key] = (Row(ob, v, cost, shown, budget, ok), where)
            last_issue = trace[-1][2]
            if f.tail_code == "OB-HOLD":
                hold = present + 2 - (last_issue + 1)
                hold_lo = hold if hold_lo is None else min(hold_lo, hold)
                if not hold_ok(hold, f):
                    viol.append(Violation("R313", f"hold: next frame may start {hold} cycles "
                                          f"after the last edge, needs at least "
                                          f"{float(f.tail_limit):.1f} ({where})", f.tail_span))
            else:
                rearm = present - last_issue
                hold_hi = rearm if hold_hi is None else max(hold_hi, rearm)
                if not rearm_ok(rearm, f):
                    viol.append(Violation("R314", f"re-arm: listening again {rearm} cycles after "
                                          f"the last sample, must be at most "
                                          f"{math.floor(f.tail_limit)} ({where})", f.tail_span))
    rows, maxn, sumn = [], Fr(0), Fr(0)
    for row, where in sorted(worst.values(), key=lambda rw: rw[0].ob.ideals[rw[0].visit]):
        rows.append(row)
        if not row.ok:
            ob = row.ob
            if ob.ref == "anchor":
                what = (f"sample in [{row.shown[0]}, {row.shown[1]}) cycles after the edge, outside "
                        f"[{float(ob.window[0]):.1f}, {float(ob.window[1]):.1f}]")
            else:
                what = (f"error {float(row.cost):.2f} cycles exceeds budget {float(row.budget):.2f}")
            tail = f"{what} (worst at {where})"
            viol.append(Violation(ob.reject, f"{ob.name} (line {ob.line}, visit {row.visit}): {tail}",
                                  ob.span, f"{ob.name}, visit {row.visit}: {tail}"))
        if row.budget > 0:
            maxn, sumn = max(maxn, row.cost / row.budget), sumn + row.cost / row.budget
    unvisited = [(ob, v) for ob in f.actions for v in range(ob.reps) if (id(ob), v) not in worst]
    return Report(rows, viol, (float(maxn), len(viol), float(sumn)), hold_lo, hold_hi, unvisited)
