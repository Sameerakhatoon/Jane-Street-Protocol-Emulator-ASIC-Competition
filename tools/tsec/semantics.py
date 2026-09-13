"""The executable semantics: what a spec promises, in exact ticks.

This module turns the timing statements of a frame into obligations, the
statements the compiler must prove, without looking at
any code. The lowering attaches each obligation to the instruction that
discharges it, the timing analysis checks it for every slot phase, and rule
R1 rejects the program if any obligation is left unproven or unvisited.

The rules:

  S1 Integer ticks. One tick is one clock cycle (1/fclk). Times are exact
     rationals until a rounding rule applies.
  S2 Eager edges. In a state, the first enabled edge in textual order fires
     at the first tick it is enabled.
  S3 Anchors. A frame starts at its anchor: `on host.pull(v)` (we own the
     next start) or `on fall(w)` / `on rise(w)` (the environment owns it).
  S4 seq desugars into a chain of timed actions. Each action has an ideal
     instant; `for T` and `each P` give the gap to the next action; actions
     are measured from the first action (the reference).
  S5 Tolerance. `~ r` (a ratio) is r times the interval before the action
     (for the first action, its own interval); `~ t` (a time) is t. Our clock
     may drift by ppm against the other side, so the budget at ideal instant
     i is tol - ppm * i (units.drift_budget).
  S6 Windows. `within [a, b] after E` is measured from the edge at the pin.
     An instruction issued D cycles after the cycle in which the synchronized
     edge appears samples the wire at an instant in [D, D + 1) ticks after the
     edge (sample_interval), and a reacting output reaches the pin in
     [D + 1 + R, D + 2 + R) ticks with R output stages (response_latency).
  S7 Hold and re-arm. After the last action, the gap minus its tolerance is
     the earliest next start. If we own the next start (S3), our last edge
     must hold at least that long (OB-HOLD); if the environment owns it, we
     must be waiting again within floor(gap - tol) ticks (OB-REARM).
"""
import math
from dataclasses import dataclass, field
from fractions import Fraction as Fr

from . import ast as A
from .diag import Span

# obligation code -> rejection code
REJECT = {"OB-EDGE": "R310", "OB-WIN": "R311", "OB-SAMP": "R312", "OB-HOLD": "R313",
          "OB-REARM": "R314"}
LANE_OUTPUT_STAGES = 2          # pin register then pad register


@dataclass(eq=False)
class Obligation:
    code: str                   # OB-EDGE, OB-SAMP, OB-WIN
    name: str                   # "shift tx": statement kind and wire
    span: Span
    kind: str                   # "out": the pin changes at issue + 1; "in": sampled at issue
    ideals: list                # ideal instant per visit, ticks on the anchor's timeline
    tol: Fr                     # allowed error, ticks, before drift
    gap: Fr                     # ticks from the last visit to the next action
    window: tuple = None        # (lo, hi) ticks after the anchor edge, OB-WIN only
    stmt: object = field(default=None, repr=False)

    @property
    def line(self):
        return self.span.line

    @property
    def ref(self):
        return "anchor" if self.window else "first"

    @property
    def reps(self):
        return len(self.ideals)

    @property
    def reject(self):
        return REJECT[self.code]


@dataclass(eq=False)
class Frame:
    """One desugared frame: anchor, timed actions, and the tail obligation."""
    role: A.Role
    anchor: A.Event
    actions: list               # Obligations in program order
    tail_code: str              # OB-HOLD or OB-REARM
    tail_gap: Fr                # gap after the last action, ticks
    tail_tol: Fr                # its tolerance, ticks
    tail_span: Span
    pushes: list                # HostIO statements after the timed actions

    @property
    def anchor_kind(self):
        return "pull" if self.anchor.kind == "named" else self.anchor.kind

    @property
    def tail_limit(self):
        return self.tail_gap - self.tail_tol


def owner_of_next_start(anchor):
    """S3: who may start the next frame."""
    return "us" if anchor.kind == "named" else "environment"


def sample_interval(d):
    """S6: an instruction issued d cycles after the synchronized edge samples in [d, d + 1)."""
    return (Fr(d), Fr(d) + 1)


def response_latency(d, stages=LANE_OUTPUT_STAGES):
    """S6: pin-to-pin latency of a reaction issued d cycles after the synchronized edge."""
    return (Fr(d + 1 + stages), Fr(d + 2 + stages))


def first_enabled(edges, enabled):
    """S2: the edge that fires is the first enabled one in textual order (or None)."""
    return next((e for e in edges if enabled(e)), None)


def tolerance(sym, stmt, base, fclk):
    t = stmt.timing.tol
    if t is None:
        return Fr(0)
    q = sym.eval(t)
    return q.v * fclk if q.dim == 1 else q.v * base


def gap_of(sym, stmt, fclk):
    t = stmt.each if isinstance(stmt, A.Shift) and stmt.each is not None else stmt.timing.for_
    return sym.eval(t).v * fclk if t is not None else Fr(0)


def name_of(stmt):
    kind = {A.Drive: "drive", A.Release: "release", A.Shift: "shift", A.Sample: "sample"}
    return f"{kind[type(stmt)]} {stmt.wire.text}"


def frame(sym, role, anchor, body, fclk):
    """S4 to S7: desugar one seq body anchored at `anchor` into obligations."""
    timed = [s for s in body if isinstance(s, A.TIMED)]
    gaps = [gap_of(sym, s, fclk) for s in timed]
    cursor, actions = Fr(0), []
    tol = Fr(0)
    for k, s in enumerate(timed):
        tol = tolerance(sym, s, gaps[k - 1] if k else gaps[k], fclk)
        window = None
        if s.timing.window is not None:
            w = s.timing.window
            window = (sym.eval(w.lo).v * fclk, sym.eval(w.hi).v * fclk)
            cursor = (window[0] + window[1]) / 2
        reps = int(sym.eval(s.count).v) if isinstance(s, A.Shift) else 1
        outgoing = isinstance(s, (A.Drive, A.Release)) or (isinstance(s, A.Shift) and s.out)
        code = "OB-WIN" if window else ("OB-EDGE" if outgoing else "OB-SAMP")
        ideals = [cursor + i * gaps[k] for i in range(reps)]
        actions.append(Obligation(code, name_of(s), s.span, "out" if outgoing else "in",
                                  ideals, tol, gaps[k], window, s))
        cursor = ideals[-1] + gaps[k]
    last = timed[-1] if timed else None
    return Frame(role, anchor, actions,
                 "OB-HOLD" if owner_of_next_start(anchor) == "us" else "OB-REARM",
                 gaps[-1] if gaps else Fr(0), tol, last.span if last else role.span,
                 [s for s in body if isinstance(s, A.HostIO) and not s.pull])


def hold_ok(hold, f):
    """S7, we own the next start: the last edge holds at least gap - tol ticks."""
    return hold >= f.tail_limit


def rearm_ok(rearm, f):
    """S7, the environment owns the next start: waiting again within floor(gap - tol)."""
    return rearm <= math.floor(f.tail_limit)
