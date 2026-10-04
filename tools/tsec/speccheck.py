"""Stage 3c, spec-level checks: mistakes visible only across edges, rules and items.

  W220 two edges of a state fire on the same event without disjoint guards
       (the first wins), or an unguarded edge hides a later one
  E221 timelock: a state's invariant expires before any exit can be taken
  E222 a timing we choose breaks a datasheet rule on an event we drive
  E240 frame layout: fields, widths, values, crc ranges, `from` roles
  E241 an enum value does not fit its type
  E250 a trigger names an event that no role emits

They run after check.py has found every name and dimension sound, so
constant evaluation here cannot fail on an unknown name.
"""
from fractions import Fraction as Fr

from . import ast as A
from . import printer
from .diag import TseError

PRODUCES = {A.Drive: "drive", A.Release: "release"}


def run(sym, errs, warns):
    overlapping_edges(sym, warns)
    timelock(sym, errs)
    datasheet_contradictions(sym, errs)
    frames(sym, errs)
    enums(sym, errs)
    triggers(sym, errs)


def const(sym, e):
    try:
        return sym.eval(e)
    except TseError:
        return None


def ns(t):
    return f"{float(t) * 1e9:.1f} ns" if t < Fr(1, 10**6) else f"{float(t) * 1e6:.3g} us"


# ---------------------------------------------------------------- W220
def conjuncts(g):
    if isinstance(g, A.Binary) and g.op == "and":
        return conjuncts(g.left) + conjuncts(g.right)
    return [g]


def disjoint(sym, g1, g2):
    """True when the guards provably never hold together: x == a against x == b (a != b)
    or x == a against x != a, for one conjunct of each."""
    for a in conjuncts(g1):
        for b in conjuncts(g2):
            if not (isinstance(a, A.Binary) and isinstance(b, A.Binary)):
                continue
            if {a.op, b.op} - {"==", "!="} or printer.expr(a.left) != printer.expr(b.left):
                continue
            va, vb = const(sym, a.right), const(sym, b.right)
            if va is None or vb is None:
                continue
            if a.op == b.op == "==" and va != vb:
                return True
            if a.op != b.op and va == vb:
                return True
    return False


def overlapping_edges(sym, warns):
    for role in sym.roles:
        for st in role.states:
            evs = [e for e in st.edges if isinstance(e, (A.OnEdge, A.RespondEdge))]
            for i, a in enumerate(evs):
                for b in evs[i + 1:]:
                    ev = printer.event(a.event)
                    if ev != printer.event(b.event):
                        continue
                    if a.guard is None:
                        warns.append(TseError("W220", f"the edge at line {b.span.line} never "
                                              f"fires: line {a.span.line} has no guard and takes "
                                              f"priority on {ev}", b.span))
                    elif b.guard is not None and not disjoint(sym, a.guard, b.guard):
                        warns.append(TseError("W220", f"edges at lines {a.span.line} and "
                                              f"{b.span.line} overlap on {ev}; line "
                                              f"{a.span.line} takes priority", b.span))


# ---------------------------------------------------------------- E221
def clock_bound(sym, g, clocks, upper):
    """(clock, bound in seconds) of `c <= T` (upper) or `c >= T` (lower) in g, else None."""
    for c in conjuncts(g):
        if not (isinstance(c, A.Binary) and isinstance(c.left, A.Ref)):
            continue
        if c.left.name.text not in clocks:
            continue
        ops = ("<=", "<") if upper else (">=", ">")
        q = const(sym, c.right)
        if c.op in ops and q is not None and q.dim == 1:
            return c.left.name.text, q.v
    return None


def seq_duration(sym, body):
    """Shortest time a seq body can take, or None if it waits for the environment."""
    total = Fr(0)
    for s in body:
        if isinstance(s, (A.Await, A.If, A.Send, A.Recv)) or (isinstance(s, A.HostIO) and s.pull):
            return None
        if isinstance(s, A.Repeat):
            inner = seq_duration(sym, s.body)
            if inner is None:
                return None
            total += inner * const(sym, s.count).v
        elif isinstance(s, A.Wait):
            total += const(sym, s.time).v
        elif isinstance(s, A.TIMED):
            if s.timing.window is not None:
                return None
            gap = s.each if isinstance(s, A.Shift) and s.each is not None else s.timing.for_
            if gap is None:
                continue
            g = const(sym, gap).v
            reps = int(const(sym, s.count).v) if isinstance(s, A.Shift) else 1
            tol = const(sym, s.timing.tol) if s.timing.tol is not None else None
            slack = Fr(0) if tol is None else (tol.v if tol.dim == 1 else tol.v * g)
            total += g * reps - slack
    return total


def timelock(sym, errs):
    for role in sym.roles:
        clocks = {c.text for c in role.clocks}
        for st in role.states:
            if st.inv is None:
                continue
            inv = clock_bound(sym, st.inv, clocks, upper=True)
            if inv is None:
                continue
            earliest = []
            for e in st.edges:
                if isinstance(e, A.AfterEdge):
                    lo = clock_bound(sym, e.guard, clocks, upper=False)
                    earliest.append((lo[1], e) if lo and lo[0] == inv[0] else (Fr(0), e))
                elif isinstance(e, A.SeqEdge):
                    d = seq_duration(sym, e.body)
                    earliest.append((d, e) if d is not None else (Fr(0), e))
                else:
                    earliest.append((Fr(0), e))
            if earliest and all(t > inv[1] for t, _ in earliest):
                t, e = min(earliest, key=lambda te: te[0])
                need = f"needs {inv[0]} >= {ns(t)}" if isinstance(e, A.AfterEdge) else \
                    f"takes at least {ns(t)}"
                errs.append(TseError("E221", f"state '{st.name.text}': invariant {inv[0]} <= "
                                     f"{ns(inv[1])}, but the earliest exit {need}", st.name.span))


# ---------------------------------------------------------------- E222
def produced(sym, s):
    """The pin event a statement makes on a wire we drive, or None."""
    if isinstance(s, A.Drive):
        v = const(sym, s.value)
        return ("rise" if v is not None and v.v == 1 else "fall"), s.wire.text
    if isinstance(s, A.Release):
        return "rise", s.wire.text
    return None


def matches(ev, made):
    kind, wire = made
    return ev.kind != "named" and ev.name.text == wire and ev.kind in (kind, "edge")


def timeline(sym, body):
    """[(statement, ideal time, tolerance)] of a straight-line body, or [] if it waits."""
    out, t = [], Fr(0)
    for s in body:
        if isinstance(s, (A.Await, A.If, A.Repeat, A.Send, A.Recv)) or \
                (isinstance(s, A.HostIO) and s.pull):
            return []
        if isinstance(s, A.Wait):
            t += const(sym, s.time).v
            continue
        if not isinstance(s, A.TIMED) or s.timing.window is not None:
            continue
        gap = s.each if isinstance(s, A.Shift) and s.each is not None else s.timing.for_
        g = const(sym, gap).v if gap is not None else Fr(0)
        tol = const(sym, s.timing.tol) if s.timing.tol is not None else None
        slack = Fr(0) if tol is None else (tol.v if tol.dim == 1 else tol.v * g)
        out.append((s, t, slack))
        reps = int(const(sym, s.count).v) if isinstance(s, A.Shift) else 1
        t += g * reps
    return out


def bodies(role):
    for st in role.states:
        for e in st.edges:
            if e.body:
                yield e.body


def datasheet_contradictions(sym, errs):
    rules = [r for d in sym.spec.of(A.Datasheet) for r in d.rules if r.guard is None]
    for role in sym.roles:
        for body in bodies(role):
            tl = timeline(sym, body)
            for i, (si, ti, toli) in enumerate(tl):
                mi = produced(sym, si)
                if mi is None:
                    continue
                for r in rules:
                    if not matches(r.a, mi):
                        continue
                    nxt = next(((sj, tj, tolj) for sj, tj, tolj in tl[i + 1:]
                                if produced(sym, sj) and matches(r.b, produced(sym, sj))), None)
                    if nxt is None:
                        continue
                    sj, tj, tolj = nxt
                    bound = const(sym, r.bound).v
                    lo, hi = tj - ti - toli - tolj, tj - ti + toli + tolj
                    strict = r.relop in (">", "<")
                    if r.relop in (">=", ">"):
                        broken = lo < bound or (strict and lo == bound)
                    else:
                        broken = hi > bound or (strict and hi == bound)
                    if broken:
                        got = lo if r.relop in (">=", ">") else hi
                        errs.append(TseError(
                            "E222", f"{printer.stmt(si, 0).rstrip(';')} then "
                            f"{printer.stmt(sj, 0).rstrip(';')}: {printer.event(r.a)} to "
                            f"{printer.event(r.b)} can be {ns(got)}, which breaks rule "
                            f"{r.name.text} ({r.relop} {ns(bound)})", sj.span))


# ---------------------------------------------------------------- E240, E241
def width_of(sym, ty):
    """Bits of a fixed-width type, or None for bytes ranges and unknown widths."""
    name = ty.name.text
    if name in ("bit", "bool"):
        return 1
    if name in sym.enums:
        return width_of(sym, sym.enums[name].type)
    if ty.width is None or ty.width_hi is not None:
        return None
    w = int(const(sym, ty.width).v)
    return 8 * w if name == "bytes" else w


def frames(sym, errs):
    roles = {r.name.text for r in sym.roles}
    for fr in sym.frames.values():
        if not fr.fields:
            errs.append(TseError("E240", f"frame '{fr.name.text}' has no fields", fr.name.span))
            continue
        seen = {}
        for k, f in enumerate(fr.fields):
            name = f.name.text
            if name in seen:
                errs.append(TseError("E240", f"field '{name}' is declared twice in frame "
                                     f"'{fr.name.text}'", f.name.span))
            seen.setdefault(name, k)
            ty = f.type
            if ty.width_hi is not None and const(sym, ty.width).v > const(sym, ty.width_hi).v:
                errs.append(TseError("E240", f"field '{name}': range {printer.type_ref(ty)} runs "
                                     "backwards", ty.span))
            w = width_of(sym, ty)
            if f.value is not None and w is not None:
                v = const(sym, f.value)
                if v is not None and not (v.dim == 0 and v.v.denominator == 1 and 0 <= v.v < 1 << w):
                    errs.append(TseError("E240", f"field '{name}': value {printer.expr(f.value)} "
                                         f"does not fit {printer.type_ref(ty)}", f.value.span))
            for a in f.attrs:
                if a.kind == "from" and a.args[0].text not in roles:
                    errs.append(TseError("E240", f"field '{name}': 'from {a.args[0].text}' names "
                                         "no role", a.args[0].span))
                elif a.kind == "crc":
                    crc_field(sym, fr, f, k, a, seen, errs)


def crc_field(sym, fr, f, k, a, seen, errs):
    crc, lo, hi = a.args
    assist = sym.assists.get(crc.text)
    if assist is None or assist.kind != "crc":
        errs.append(TseError("E240", f"field '{f.name.text}': '{crc.text}' is not a crc assist",
                             crc.span))
        return
    for end in (lo, hi):
        if end.text not in seen or seen[end.text] >= k:
            errs.append(TseError("E240", f"field '{f.name.text}': crc range end '{end.text}' is not "
                                 "a field before this one", end.span))
            return
    if seen[lo.text] > seen[hi.text]:
        errs.append(TseError("E240", f"crc range '{lo.text}..{hi.text}' runs backwards", lo.span))
    width = assist.get("width")
    w = width_of(sym, f.type)
    if width is not None and w is not None:
        cw = const(sym, width.value)
        if cw is not None and cw.v != w:
            errs.append(TseError("E240", f"field '{f.name.text}' is {printer.type_ref(f.type)} but "
                                 f"crc '{crc.text}' has width {cw.v}", f.type.span))


def enums(sym, errs):
    for en in sym.enums.values():
        w = width_of(sym, en.type)
        for m in en.members:
            v = const(sym, m.value)
            if w is not None and v is not None and not (v.dim == 0 and v.v.denominator == 1
                                                        and 0 <= v.v < 1 << w):
                errs.append(TseError("E241", f"enum '{en.name.text}': {m.name.text} = "
                                     f"{printer.expr(m.value)} does not fit "
                                     f"{printer.type_ref(en.type)}", m.value.span))


# ---------------------------------------------------------------- E250
def triggers(sym, errs):
    emitted = {(role, e.text) for role, names in sym.emits.items() for e in names}
    names = {e for _, e in emitted}
    for tr in sym.triggers:
        for te in tr.events:
            ev = te.event
            if ev.kind != "named":
                if ev.name.text not in sym.wires:
                    errs.append(TseError("E204", f"unknown wire '{ev.name.text}'", ev.name.span))
                continue
            if ev.attr is not None:
                ok = (ev.name.text, ev.attr.text) in emitted or \
                    (ev.name.text == sym.spec.name.text and ev.attr.text in names)
            else:
                ok = ev.name.text in names
            if not ok:
                errs.append(TseError("E250", f"trigger '{tr.name.text}' uses "
                                     f"{printer.event(ev)}, which no role emits", ev.span))
            if te.within is not None:
                q = const(sym, te.within)
                if q is not None and q.dim != 1:
                    errs.append(TseError("E211", "'within' needs a time", te.within.span))
