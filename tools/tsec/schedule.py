# SPDX-License-Identifier: Apache-2.0
"""Stage 5a, scheduling: labels, the delay solver and the pacing strategy search.

Issue times are monotone in each delay, so coordinate descent works: for each
tunable DLY try nearby values and keep the one that minimises the worst
normalised error over every obligation, phase and buffer state (ties broken by
the number of violations, then by the sum of normalised errors). For timer
pacing the timer's start phase is searched as well, in quarter cycles.
"""
from dataclasses import dataclass, field
from fractions import Fraction as Fr

from . import isa
from .diag import TseError
from .fsmsched import Scheduler
from . import assist_events, frametime     # frame roles
from .lower import Lowering, frame_shape
from .timing import Violation, analyse

STRATEGIES = ("dly", "timer")
HINTS = {
    "dly": "a DLY-paced loop has a whole-cycle period (a multiple of S = {S} if it spans more "
           "than {entries} word(s)); the series needs its ideal period",
    "overrun": "the loop body needs more cycles than one period of {period:.2f} cycles; lower "
               "the rate or use a hardware assist",
    "timer": "timer-paced edges still land on whole cycles (up to half a cycle of error); lower "
             "the rate, widen the tolerance or use the TX shift register",
}


def resolve_labels(prog):
    labels = {}
    for i, ins in enumerate(prog):
        if ins.label:
            labels[ins.label] = i
    for i, ins in enumerate(prog):        # "expect 1" skips point past the error flag
        if ins.op == "JMP" and ins.a["target"].startswith("ok") and ins.a["target"] not in labels:
            labels[ins.a["target"]] = i + 2
    return labels


def solve(low, labels, lane, fm, ppm):
    tun = [i for i, ins in enumerate(low.prog) if ins.tune]
    phases = [Fr(k, 4) for k in range(-12, 13)] if low.nco_period else [Fr(0)]
    best_phase = Fr(0)
    best = analyse(low, labels, lane, fm, ppm, best_phase)
    for _ in range(6):
        improved = False
        for i in tun:
            cur = low.prog[i].a["d"]
            for v in range(max(0, cur - 10), min(isa.DLY_MAX, cur + 10) + 1):
                low.prog[i].a["d"] = v
                r = analyse(low, labels, lane, fm, ppm, best_phase)
                if r.cost < best.cost:
                    best, cur, improved = r, v, True
            low.prog[i].a["d"] = cur
        for ph in phases:
            r = analyse(low, labels, lane, fm, ppm, ph)
            if r.cost < best.cost:
                best, best_phase, improved = r, ph, True
        if not improved:
            break
    best = analyse(low, labels, lane, fm, ppm, best_phase)
    low.nco_phase = best_phase
    return best


@dataclass
class Attempt:
    mode: str
    low: Lowering
    labels: dict
    report: object
    hint: str = ""


@dataclass
class RoleResult:
    role: object
    lane: int
    chosen: Attempt = None
    tried: list = field(default_factory=list)
    kind: str = "frame"


def hint(att, fm):
    if att.mode == "dly":
        return HINTS["dly"].format(S=fm.S, entries=fm.entries)
    if any(v.code == "R317" for v in att.report.violations):
        return HINTS["overrun"].format(period=float(att.low.nco_period or 0))
    return HINTS["timer"]


def compile_role(sym, role, lane, fclk, fm, ppm, pads=(0, 25), window=(0, 128)):
    """Try each pacing strategy in order; the first that proves every obligation wins.
    A role outside the paced-frame shape goes to the state-machine lowering instead.
    Raises TseError for shapes and resources the lowering cannot handle (R300, R301, R307)."""
    if assist_events.is_frame_role(sym, role):        # frame roles: send, recv, assist events
        return frametime.compile_role(sym, role, lane, fclk, fm, ppm, pads)
    res = RoleResult(role, lane)
    try:
        frame_shape(role)
    except TseError as e:
        if e.code != "R300":
            raise
        res.kind = "fsm"
        res.chosen, res.tried = Scheduler(sym, role, lane, fclk, fm, pads, ppm, window).compile()
        return res
    for mode in STRATEGIES:
        low = Lowering(sym, role, fclk, mode)
        low.pads = (Fr(pads[0]) * fclk / 10**9, Fr(pads[1]) * fclk / 10**9)
        low.lower()
        if len(low.prog) > isa.MAX_INSTRUCTIONS:
            raise TseError("R301", f"program too large: {len(low.prog)} instructions, a lane holds "
                           f"{isa.MAX_INSTRUCTIONS} (8-bit jump targets)", role.name.span)
        labels = resolve_labels(low.prog)
        rep = solve(low, labels, lane, fm, ppm)
        for ob, v in rep.unvisited:          # R1: an obligation the path never reached is unproven
            rep.violations.append(Violation(ob.reject, f"{ob.name} (line {ob.line}, visit {v}): "
                                            "never reached on the analysed path, so it is "
                                            "unproven", ob.span))
        att = Attempt(mode, low, labels, rep)
        att.hint = hint(att, fm)
        res.tried.append(att)
        if not rep.violations:
            res.chosen = att
            return res
    return res
