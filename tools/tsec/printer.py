"""Pretty printer: syntax tree back to canonical TSE text.

Used by `tsec fmt` and by the round-trip test parse(print(parse(s))) == parse(s).
Parentheses are printed only where precedence needs them.
"""
from . import ast as A

PREC = {"or": 1, "and": 2, "not": 3, "==": 4, "!=": 4, "<": 4, "<=": 4, ">": 4, ">=": 4,
        "+": 5, "-": 5, "|": 5, "*": 6, "/": 6, "&": 6, "<<": 6, ">>": 6, "neg": 7}


def number(v):
    if v.denominator == 1:
        return str(v.numerator)
    return format(float(v), "g") if float(v) == v else f"({v.numerator} / {v.denominator})"


def expr(e, outer=0):
    if isinstance(e, A.Num):
        s = e.text or number(e.value)
        if e.percent:
            s += " %"
        elif e.unit:
            s += " " + e.unit.text
        return s
    if isinstance(e, A.Ref):
        s = e.name.text
        if e.index is not None:
            s += "[" + expr(e.index) + (":" + expr(e.index_lo) if e.index_lo is not None else "") + "]"
        if e.attr:
            s += "." + e.attr.text
        return s
    if isinstance(e, A.Unary):
        p = PREC["not" if e.op == "not" else "neg"]
        s = ("not " if e.op == "not" else "-") + expr(e.operand, p)
    else:
        p = PREC[e.op]
        # left associative: the right operand needs parentheses at equal precedence
        s = f"{expr(e.left, p)} {e.op} {expr(e.right, p + 1)}"
    return f"({s})" if p < outer else s


def type_ref(t):
    return t.name.text + (f"[{expr(t.width)}]" if t.width is not None else "")


def event(ev):
    if ev.kind != "named":
        return f"{ev.kind}({ev.name.text})"
    s = ev.name.text + (f".{ev.attr.text}" if ev.attr else "")
    if ev.args:
        s += "(" + ", ".join(expr(a) for a in ev.args) + ")"
    return s


def timing(t):
    s = ""
    if t.window:
        s += f" within [{expr(t.window.lo)}, {expr(t.window.hi)}] after {event(t.window.after)}"
    if t.for_ is not None:
        s += f" for {expr(t.for_)}"
    if t.tol is not None:
        s += f" ~ {expr(t.tol)}"
    return s


def block(stmts, depth):
    pad = "  " * depth
    lines = ["{"]
    for st in stmts:
        lines.append(pad + "  " + stmt(st, depth + 1))
    lines.append(pad + "}")
    return "\n".join(lines)


def stmt(s, depth):
    if isinstance(s, A.Drive):
        return f"drive {s.wire.text} = {expr(s.value)}{timing(s.timing)};"
    if isinstance(s, A.Release):
        return f"release {s.wire.text}{timing(s.timing)};"
    if isinstance(s, A.Shift):
        pins = s.wires[0].text if len(s.wires) == 1 else "(" + ", ".join(w.text for w in s.wires) + ")"
        core = (f"shift {s.var.text} {s.order} {expr(s.count)} -> {pins}" if s.out
                else f"shift {pins} -> {s.var.text} {s.order} {expr(s.count)}")
        if s.each is not None:
            tol = f" ~ {expr(s.timing.tol)}" if s.timing.tol is not None else ""
            return f"{core} each {expr(s.each)}{tol};"
        return f"{core}{timing(s.timing)};"
    if isinstance(s, A.Sample):
        exp = f" expect {expr(s.expect)}" if s.expect is not None else ""
        return f"sample {s.wire.text}{exp}{timing(s.timing)};"
    if isinstance(s, A.Await):
        return (f"await {event(s.event)}" + (f" when {expr(s.guard)}" if s.guard is not None else "")
                + (f" timeout {expr(s.timeout)}" if s.timeout is not None else "") + ";")
    if isinstance(s, A.Repeat):
        return f"repeat {expr(s.count)} {block(s.body, depth)}"
    if isinstance(s, A.If):
        out = f"if {expr(s.guard)} {block(s.then, depth)}"
        return out + (f" else {block(s.orelse, depth)}" if s.orelse is not None else "")
    if isinstance(s, A.Wait):
        return f"wait {expr(s.time)};"
    if isinstance(s, A.HostIO):
        return f"{'pull' if s.pull else 'push'} host {s.var.text};"
    if isinstance(s, A.Emit):
        args = "(" + ", ".join(expr(a) for a in s.args) + ")" if s.args else ""
        return f"emit {s.name.text}{args};"
    if isinstance(s, A.Reset):
        return "reset " + ", ".join(n.text for n in s.names) + ";"
    if isinstance(s, A.Set):
        return f"set {s.name.text} = {expr(s.value)};"
    raise TypeError(f"no printer for {type(s).__name__}")


def edge(e, depth):
    guard = f" when {expr(e.guard)}" if getattr(e, "guard", None) is not None else ""
    body = f" do {block(e.body, depth)}" if getattr(e, "body", None) is not None else ""
    if isinstance(e, A.OnEdge):
        return f"on {event(e.event)}{guard}{body} -> {e.target.text};"
    if isinstance(e, A.AfterEdge):
        return f"after {expr(e.guard)}{body} -> {e.target.text};"
    if isinstance(e, A.SeqEdge):
        abort = f" abort on {event(e.abort_on)} -> {e.abort_to.text}" if e.abort_on else ""
        return f"seq {block(e.body, depth)} -> {e.target.text}{abort};"
    return (f"respond {e.name.text} : on {event(e.event)}{guard} {block(e.body, depth)} "
            f"within [{expr(e.lo)}, {expr(e.hi)}] -> {e.target.text};")


def wire(w):
    attrs = "".join(f" {a.name.text}" + (f" {expr(a.value)}" if a.value is not None else "")
                    for a in w.attrs)
    return f"wire {w.name.text} : {w.dir}{attrs};"


def role(r):
    uses = ", ".join(u.text for u in r.uses)
    lane = f" lane {expr(r.lane)}" if r.lane is not None else ""
    out = [f"  role {r.name.text} : {r.kind} uses {uses}{lane} {{"]
    for v in r.vars:
        init = f" = {expr(v.init)}" if v.init is not None else ""
        out.append(f"    var {v.name.text} : {type_ref(v.type)}{init};")
    for a in r.assumes:
        rel = f" -> {event(a.b)} {a.relop} {expr(a.bound)}" if a.b else ""
        out.append(f"    assume {a.name.text} : {event(a.a)}{rel};")
    for st in r.states:
        head = ("initial " if st.initial else "") + f"state {st.name.text}"
        head += f" inv {expr(st.inv)}" if st.inv is not None else ""
        out.append(f"    {head} {{")
        out += [f"      {edge(e, 3)}" for e in st.edges]
        out.append("    }")
    out.append("  }")
    return "\n".join(out)


def item(it):
    if isinstance(it, A.Param):
        kw = "const" if it.const else "param"
        return f"  {kw} {it.name.text} : {type_ref(it.type)} = {expr(it.value)};"
    if isinstance(it, A.UnitDef):
        return f"  unit {it.name.text} = {expr(it.value)};"
    if isinstance(it, A.Wire):
        return "  " + wire(it)
    if isinstance(it, A.EventDef):
        guard = f" when {expr(it.guard)}" if it.guard is not None else ""
        return f"  event {it.name.text} = {event(it.event)}{guard};"
    if isinstance(it, A.Datasheet):
        lines = [f"  datasheet {it.name.text} {{"]
        for r in it.rules:
            guard = f" when {expr(r.guard)}" if r.guard is not None else ""
            lines.append(f"    rule {r.name.text} : {event(r.a)} -> {event(r.b)} {r.relop} "
                         f"{expr(r.bound)}{guard};")
        return "\n".join(lines + ["  }"])
    return role(it)


def spec(s):
    return "\n".join([f"protocol {s.name.text} {{"] + [item(i) for i in s.items] + ["}"]) + "\n"
