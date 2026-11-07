# SPDX-License-Identifier: Apache-2.0
"""The "fix" line of a rejection: solve a failing bound for the params it depends on.

A window bound or a datasheet bound is an expression over params (t_css -
t_su, 1 / (2 * f_sck) - t_mo). For each param it depends on, the bound is
re-evaluated with that param overridden (the whole spec is resolved again,
so units built from params follow) and the value where the bound just meets
the requirement is found by bisection over a factor of 1000 either way. The
answer is rounded in the safe direction to three significant digits.
"""
from fractions import Fraction as Fr

from . import ast as A
from .diag import Errors, TseError
from .resolve import resolve

SI = {1: [(Fr(1, 10**9), "ns"), (Fr(1, 10**6), "us"), (Fr(1, 10**3), "ms"), (Fr(1), "s")],
      -1: [(Fr(1), "Hz"), (Fr(10**3), "kHz"), (Fr(10**6), "MHz")]}


def show(v, dim):
    """A quantity in the largest unit that keeps it at or above 1."""
    if dim not in SI:
        return f"{float(v):.3g}"
    scale, unit = SI[dim][0]
    for s, u in SI[dim]:
        if abs(v) >= s:
            scale, unit = s, u
    return f"{float(v / scale):.3g} {unit}"


def names_in(e, out=None):
    out = set() if out is None else out
    if isinstance(e, A.Ref):
        out.add(e.name.text)
    elif isinstance(e, A.Num) and e.unit is not None:
        out.add(e.unit.text)
    elif isinstance(e, A.Unary):
        names_in(e.operand, out)
    elif isinstance(e, A.Binary):
        names_in(e.left, out)
        names_in(e.right, out)
    elif isinstance(e, A.Call):
        for a in e.args:
            names_in(a, out)
    return out


def params_of(sym, e):
    """The overridable params an expression depends on, through units too."""
    todo, seen, out = list(names_in(e)), set(), []
    units = {u.name.text: u for u in sym.spec.of(A.UnitDef)}
    while todo:
        n = todo.pop()
        if n in seen:
            continue
        seen.add(n)
        p = next((it for it in sym.spec.of(A.Param) if it.name.text == n), None)
        if p is not None and not p.const:
            out.append(n)
        elif n in units:
            todo += list(names_in(units[n].value))
    return sorted(out)


def evaluate(sym, e, overrides):
    try:
        s2 = resolve(sym.spec, {k: str(v) for k, v in overrides.items()})
        q = s2.eval(e)
    except (Errors, TseError, ZeroDivisionError):
        return None
    return q.v


def solve(sym, e, need, at_least, base_overrides=None):
    """[(param, 'p <= X' text)] such that value(e) >= need (at_least) or <= need."""
    out = []
    over = dict(base_overrides or {})
    for p in params_of(sym, e):
        cur = sym.params[p][1]
        if cur.v == 0:
            continue

        def ok(f):
            v = evaluate(sym, e, {**over, p: cur.v * f})
            return v is not None and (v >= need if at_least else v <= need)
        if ok(Fr(1)):
            continue
        lo_f, hi_f = None, None
        for f in (Fr(1, 1000), Fr(1000)):
            if ok(f):
                lo_f, hi_f = Fr(1), f
                break
        if lo_f is None:
            continue
        for _ in range(60):             # lo_f fails, hi_f passes
            mid = (lo_f + hi_f) / 2
            if ok(mid):
                hi_f = mid
            else:
                lo_f = mid
        bound = cur.v * hi_f
        up = hi_f > 1
        v = safe_round(bound, up)
        out.append((p, f"{p} {'>=' if up else '<='} {show(v, cur.dim)}"))
    return out


def safe_round(v, up):
    """Three significant digits, away from the failing side."""
    if v == 0:
        return v
    digits = 3
    mag = Fr(10) ** (len(str(int(abs(v)))) - digits) if abs(v) >= 1 else None
    if mag is None:
        k = 0
        while abs(v) * Fr(10) ** k < 10 ** (digits - 1):
            k += 1
        mag = Fr(1, 10 ** k)
    q = v / mag
    n = -((-q.numerator) // q.denominator) if up else q.numerator // q.denominator
    return n * mag
