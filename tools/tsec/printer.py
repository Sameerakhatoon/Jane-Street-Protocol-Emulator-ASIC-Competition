# SPDX-License-Identifier: Apache-2.0
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
    if isinstance(e, A.Call):
        return e.name.text + "(" + ", ".join(expr(a) for a in e.args) + ")"
    if isinstance(e, A.Tuple):
        return "(" + ", ".join(expr(a) for a in e.items) + ")"
    if isinstance(e, A.Unary):
        p = PREC["not" if e.op == "not" else "neg"]
        s = ("not " if e.op == "not" else "-") + expr(e.operand, p)
    else:
        p = PREC[e.op]
        # left associative: the right operand needs parentheses at equal precedence
        s = f"{expr(e.left, p)} {e.op} {expr(e.right, p + 1)}"
    return f"({s})" if p < outer else s


def type_ref(t):
    if t.width is None:
        return t.name.text
    hi = f"..{expr(t.width_hi)}" if t.width_hi is not None else ""
    return f"{t.name.text}[{expr(t.width)}{hi}]"


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
    if isinstance(s, A.Load):
        return f"load {s.var.text} from {s.array.text}[{expr(s.index)}];"
    if isinstance(s, A.Store):
        return f"store {s.var.text} to {s.array.text}[{expr(s.index)}];"
    if isinstance(s, A.Send):
        args = "(" + ", ".join(expr(a) for a in s.args) + ")" if s.args else ""
        return f"send {s.name.text}{args}{timing(A.Timing(s.window))};"
    if isinstance(s, A.Recv):
        return f"recv {s.name.text}" + (f" into {s.into.text}" if s.into else "") + ";"
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
    out = [f"  role {r.name.text} : {r.kind} uses {uses}{lane} {{"] + role_body(r, "    ")
    out.append("  }")
    return "\n".join(out)


def role_body(r, pad):
    out = []
    for v in r.vars:
        init = f" = {expr(v.init)}" if v.init is not None else ""
        out.append(f"{pad}var {v.name.text} : {type_ref(v.type)}{init};")
    if r.clocks:
        out.append(f"{pad}clock " + ", ".join(c.text for c in r.clocks) + ";")
    for a in r.assumes:
        rel = f" -> {event(a.b)} {a.relop} {expr(a.bound)}" if a.b else ""
        out.append(f"{pad}assume {a.name.text} : {event(a.a)}{rel};")
    depth = len(pad) // 2
    for st in r.states:
        head = ("initial " if st.initial else "") + f"state {st.name.text}"
        head += f" inv {expr(st.inv)}" if st.inv is not None else ""
        out.append(f"{pad}{head} {{")
        out += [f"{pad}  {edge(e, depth + 1)}" for e in st.edges]
        out.append(f"{pad}}}")
    for c in r.conds:
        out.append(f"{pad}if {expr(c.cond)} {{")
        out += role_body(c.then, pad + "  ")
        if c.orelse is not None:
            out.append(f"{pad}}} else {{")
            out += role_body(c.orelse, pad + "  ")
        out.append(f"{pad}}}")
    return out


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
    if isinstance(it, A.EnumDef):
        members = ", ".join(f"{m.name.text} = {expr(m.value)}" for m in it.members)
        return f"  enum {it.name.text} : {type_ref(it.type)} {{ {members} }}"
    if isinstance(it, A.Frame):
        return "\n".join([f"  frame {it.name.text} {{"] + [f"    {field(f)}" for f in it.fields]
                         + ["  }"])
    if isinstance(it, A.Assist):
        lines = [f"  assist {it.name.text} : {it.kind} {{"]
        lines += [f"    {p.name.text} = {expr(p.value)};" for p in it.params]
        return "\n".join(lines + ["  }"])
    if isinstance(it, A.Trigger):
        evs = " then ".join(tevent(te) for te in it.events)
        return f"  trigger {it.name.text} {{ match {evs} action {stmt(it.action, 2)} }}"
    if isinstance(it, A.ItemIf):
        out = [f"  if {expr(it.cond)} {{"] + [item(i) for i in it.then]
        if it.orelse is not None:
            out += ["  } else {"] + [item(i) for i in it.orelse]
        return "\n".join(out + ["  }"])
    return role(it)


def field(f):
    s = f"{f.name.text} : {type_ref(f.type)}" + (f" = {expr(f.value)}" if f.value is not None else "")
    for a in f.attrs:
        if a.kind == "nostuff":
            s += " nostuff"
        elif a.kind == "from":
            s += f" from {a.args[0].text}"
        elif a.kind == "if":
            s += f" if {expr(a.args[0])}"
        else:
            s += f" crc {a.args[0].text} over {a.args[1].text}..{a.args[2].text}"
    return s + ";"


def tevent(te):
    s = event(te.event)
    if te.guard is not None:
        s += f" when {expr(te.guard)}"
    if te.within is not None:
        s += f" within {expr(te.within)}"
    return s


def spec(s):
    return "\n".join([f"protocol {s.name.text} {{"] + [item(i) for i in s.items] + ["}"]) + "\n"
