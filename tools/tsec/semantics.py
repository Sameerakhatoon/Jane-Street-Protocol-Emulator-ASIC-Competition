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
  S8 Environment separations. A datasheet rule or an `assume` line
     `A -> B >= T` says the next B after any A comes at least T later. It
     applies to an event a when every a is an A (rule A = edge(w) covers
     rise(w); a rule guarded by `w == v` applies when w is known to be v).
     With no direct rule, a -> b may chain through the opposite edge of b's
     wire when a leaves that wire at b's level (rise -> fall -> rise), or
     through the edge that makes b's guard true (fall(scl) -> rise(scl) ->
     stop, for stop = rise(sda) when scl == 1). With
     no rule at all the separation is 0: the next b may already be here.
     An edge visible in cycle k at the synchronizer was at the pin in
     [k - 2, k - 1), so the next b is visible no earlier than k + floor(T).
"""
import math
from dataclasses import dataclass, field
from fractions import Fraction as Fr

from . import ast as A
from . import printer
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


# ---------------------------------------------------------------- S8
def base_event(sym, e):
    """(pin event, guard) behind e: a named event's definition, or e itself."""
    if e.kind == "named" and e.attr is None and e.name.text in sym.events:
        d = sym.events[e.name.text]
        return d.event, d.guard
    return e, None


def level_guard(sym, g):
    """{wire: level} of a guard made of `w == 0|1` terms joined by and, else None."""
    if g is None:
        return {}
    if isinstance(g, A.Binary) and g.op == "and":
        a, b = level_guard(sym, g.left), level_guard(sym, g.right)
        return None if a is None or b is None else {**a, **b}
    if isinstance(g, A.Binary) and g.op == "==" and isinstance(g.left, A.Ref) \
            and g.left.name.text in sym.wires and g.left.index is None:
        try:
            v = sym.eval(g.right).v
        except Exception:
            return None
        return {g.left.name.text: int(v)} if v in (0, 1) else None
    return None


def covers(sym, r, e, ctx=None):
    """Every occurrence of e (with wire levels ctx at that instant) is an occurrence of r."""
    ctx = ctx or {}
    if r.kind == "named":
        if e.kind == "named":
            return printer.event(r) == printer.event(e)
        base, guard = base_event(sym, r)
        if base is r:
            return False
        lv = level_guard(sym, guard)
        return lv is not None and covers(sym, base, e, ctx) and all(ctx.get(w) == v for w, v in lv.items())
    base, guard = base_event(sym, e)
    if base is not e:
        lv = level_guard(sym, guard) or {}
        return covers(sym, r, base, {**ctx, **lv})
    if r.kind == "taken" or e.kind == "taken" or r.name.text != e.name.text:
        return r.kind == e.kind == "taken" and r.name.text == e.name.text
    return r.kind == e.kind or r.kind == "edge"


def env_rules(sym, role):
    """(A, B, bound in seconds, guard levels, name) of every lower-bound rule and assume."""
    out = []
    for d in sym.spec.of(A.Datasheet):
        for r in d.rules:
            lv = level_guard(sym, r.guard)
            if r.relop in (">=", ">") and lv is not None:
                out.append((r.a, r.b, sym.eval(r.bound).v, lv, r))
    for a in role.assumes if role is not None else ():
        if a.b is not None and a.relop in (">=", ">"):
            out.append((a.a, a.b, sym.eval(a.bound).v, {}, a))
    return out


def post_level(sym, e, wire):
    """Level of wire right after event e, when e fixes it."""
    base, guard = base_event(sym, e)
    if base.kind in ("rise", "fall") and base.name.text == wire:
        return 1 if base.kind == "rise" else 0
    return (level_guard(sym, guard) or {}).get(wire)


def direct_separation(sym, role, a, b, fclk, ctx=None):
    best, used = Fr(0), []
    ctx = dict(ctx or {})
    base, guard = base_event(sym, a)
    ctx.update(level_guard(sym, guard) or {})
    for ra, rb, t, lv, rule in env_rules(sym, role):
        if not all(ctx.get(w) == v for w, v in lv.items()):
            continue
        if covers(sym, ra, a, ctx) and covers(sym, rb, b) and t * fclk > best:
            best, used = t * fclk, [rule]
    return best, used


def min_separation(sym, role, a, b, fclk, ctx=None):
    """S8: (ticks, rules) from event a to the next event b, as the environment guarantees."""
    best, used = direct_separation(sym, role, a, b, fclk, ctx)
    if used:
        return best, used
    base, _ = base_event(sym, b)
    if base.kind in ("rise", "fall"):
        w = base.name.text
        if post_level(sym, a, w) == (1 if base.kind == "rise" else 0) or \
                (ctx or {}).get(w) == (1 if base.kind == "rise" else 0):
            opp = A.Event("fall" if base.kind == "rise" else "rise", base.name)
            t1, r1 = direct_separation(sym, role, a, opp, fclk, ctx)
            t2, r2 = direct_separation(sym, role, opp, b, fclk)
            if r1 and r2:
                return t1 + t2, r1 + r2
    _, guard = base_event(sym, b)        # stop = rise(sda) when scl == 1, after fall(scl)
    for w, v in (level_guard(sym, guard) or {}).items():
        if post_level(sym, a, w) == 1 - v or (ctx or {}).get(w) == 1 - v:
            via = A.Event("rise" if v else "fall", A.Ident(w))
            t1, r1 = direct_separation(sym, role, a, via, fclk, ctx)
            t2, r2 = direct_separation(sym, role, via, b, fclk)
            if r1 and r2:
                return t1 + t2, r1 + r2
    return Fr(0), []


def opposite(e):
    """The edge that undoes pin event e (rise <-> fall), or any change for edge(w)."""
    return A.Event({"rise": "fall", "fall": "rise"}.get(e.kind, "edge"), e.name)


def stable_after(sym, role, e, wire, fclk):
    """Ticks after event e during which wire cannot change, from the rules (min over its levels)."""
    known = post_level(sym, e, wire)
    worst = None
    for lvl in ((known,) if known is not None else (0, 1)):
        nxt = A.Event("fall" if lvl == 1 else "rise", A.Ident(wire))
        t, used = direct_separation(sym, role, e, nxt, fclk, {wire: lvl})
        if not used:
            t, used = direct_separation(sym, role, e, A.Event("edge", A.Ident(wire)), fclk, {wire: lvl})
        if not used:
            return Fr(0)
        worst = t if worst is None else min(worst, t)
    return worst
