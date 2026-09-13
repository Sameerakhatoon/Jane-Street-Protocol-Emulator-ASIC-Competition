"""Syntax tree for TSE.

Plain dataclasses, one per grammar rule. Every node carries a source span
that is excluded from equality, so the round-trip test can compare trees
parsed from different texts. Expressions stay trees until stage 3 (resolve),
so -D overrides and messages can still see the original text.
"""
from dataclasses import dataclass, field, fields, is_dataclass
from fractions import Fraction

from .diag import NOWHERE, Span


def _span():
    return field(default=NOWHERE, compare=False, repr=False)


@dataclass
class Ident:
    text: str
    span: Span = _span()


# ---------------------------------------------------------------- expressions
@dataclass
class Num:
    value: Fraction
    unit: Ident = None          # 4.7 us, 1 bit
    percent: bool = False       # 2 %
    text: str = field(default="", compare=False, repr=False)
    span: Span = _span()


@dataclass
class Ref:
    name: Ident
    index: object = None        # v[7] or v[7:1]
    index_lo: object = None
    attr: Ident = None          # host.window_loaded
    span: Span = _span()


@dataclass
class Unary:
    op: str                     # "-" or "not"
    operand: object
    span: Span = _span()


@dataclass
class Binary:
    op: str
    left: object
    right: object
    span: Span = _span()


@dataclass
class TypeRef:
    name: Ident
    width: object = None        # bits[8], uint[4]
    span: Span = _span()


# ---------------------------------------------------------------- events
@dataclass
class Event:
    kind: str                   # rise, fall, edge, taken, or named
    name: Ident                 # the wire, or the event's first name
    attr: Ident = None          # host.pull
    args: list = field(default_factory=list)
    span: Span = _span()


@dataclass
class Window:
    lo: object
    hi: object
    after: Event
    span: Span = _span()


# ---------------------------------------------------------------- statements
@dataclass
class Timing:
    window: Window = None
    for_: object = None
    tol: object = None


@dataclass
class Drive:
    wire: Ident
    value: object
    timing: Timing = field(default_factory=Timing)
    span: Span = _span()


@dataclass
class Release:
    wire: Ident
    timing: Timing = field(default_factory=Timing)
    span: Span = _span()


@dataclass
class Shift:
    out: bool                   # True: var -> wire, False: wire -> var
    var: Ident
    order: str                  # lsb or msb
    count: object
    wires: list
    each: object = None
    timing: Timing = field(default_factory=Timing)
    span: Span = _span()

    @property
    def wire(self):
        return self.wires[0]


@dataclass
class Sample:
    wire: Ident
    expect: object = None
    timing: Timing = field(default_factory=Timing)
    span: Span = _span()


@dataclass
class Await:
    event: Event
    guard: object = None
    timeout: object = None
    span: Span = _span()


@dataclass
class Repeat:
    count: object
    body: list
    span: Span = _span()


@dataclass
class If:
    guard: object
    then: list
    orelse: list = None
    span: Span = _span()


@dataclass
class Wait:
    time: object
    span: Span = _span()


@dataclass
class HostIO:
    pull: bool                  # pull host v / push host v
    var: Ident
    span: Span = _span()


@dataclass
class Emit:
    name: Ident
    args: list = field(default_factory=list)
    span: Span = _span()


@dataclass
class Reset:
    names: list
    span: Span = _span()


@dataclass
class Set:
    name: Ident
    value: object
    span: Span = _span()


TIMED = (Drive, Release, Shift, Sample)


# ---------------------------------------------------------------- edges, states
@dataclass
class OnEdge:
    event: Event
    guard: object = None
    body: list = None
    target: Ident = None
    span: Span = _span()


@dataclass
class AfterEdge:
    guard: object
    body: list = None
    target: Ident = None
    span: Span = _span()


@dataclass
class SeqEdge:
    body: list
    target: Ident
    abort_on: Event = None
    abort_to: Ident = None
    span: Span = _span()


@dataclass
class RespondEdge:
    name: Ident
    event: Event
    guard: object
    body: list
    lo: object
    hi: object
    target: Ident
    span: Span = _span()


@dataclass
class State:
    name: Ident
    edges: list
    initial: bool = False
    inv: object = None
    span: Span = _span()


# ---------------------------------------------------------------- items
@dataclass
class Param:
    name: Ident
    type: TypeRef
    value: object
    const: bool = False
    span: Span = _span()


@dataclass
class UnitDef:
    name: Ident
    value: object
    span: Span = _span()


@dataclass
class WireAttr:
    name: Ident
    value: object = None


@dataclass
class Wire:
    name: Ident
    dir: str                    # in, out, inout
    attrs: list = field(default_factory=list)
    span: Span = _span()

    def attr(self, name):
        return next((a for a in self.attrs if a.name.text == name), None)

    @property
    def open_drain(self):
        return self.attr("open_drain") is not None


@dataclass
class EventDef:
    name: Ident
    event: Event
    guard: object = None
    span: Span = _span()


@dataclass
class Rule:
    name: Ident
    a: Event
    b: Event
    relop: str
    bound: object
    guard: object = None
    span: Span = _span()


@dataclass
class Datasheet:
    name: Ident
    rules: list
    span: Span = _span()


@dataclass
class Var:
    name: Ident
    type: TypeRef
    init: object = None
    span: Span = _span()


@dataclass
class Assume:
    name: Ident
    a: Event
    b: Event = None             # relational form: a -> b relop bound
    relop: str = None
    bound: object = None
    span: Span = _span()


@dataclass
class Role:
    name: Ident
    kind: str                   # controller, target, monitor
    uses: list
    lane: object = None
    vars: list = field(default_factory=list)
    assumes: list = field(default_factory=list)
    states: list = field(default_factory=list)
    span: Span = _span()


@dataclass
class Spec:
    name: Ident
    items: list = field(default_factory=list)
    span: Span = _span()

    def of(self, cls):
        return [i for i in self.items if isinstance(i, cls)]


# ---------------------------------------------------------------- tree dump
def dump(node, indent=0):
    """Indented tree, one node per line, scalars inline, spans as @line:col."""
    from .printer import expr as show_expr
    pad = "  " * indent
    if isinstance(node, list):
        return "\n".join(dump(n, indent) for n in node)
    if isinstance(node, (Num, Ref, Unary, Binary)):
        return f"{pad}{show_expr(node)}"
    if isinstance(node, Ident):
        return f"{pad}{node.text}"
    head, kids = [type(node).__name__], []
    for f in fields(node):
        v = getattr(node, f.name)
        if f.name == "span" or v is None or v == [] or v is False:
            continue
        if isinstance(v, Ident):
            head.append(f"{f.name}={v.text}")
        elif isinstance(v, (Num, Ref, Unary, Binary)):
            head.append(f"{f.name}=({show_expr(v)})")
        elif isinstance(v, TypeRef):
            from .printer import type_ref
            head.append(f"{f.name}={type_ref(v)}")
        elif isinstance(v, list) and all(isinstance(x, Ident) for x in v):
            head.append(f"{f.name}=[{', '.join(x.text for x in v)}]")
        elif isinstance(v, Timing):
            if v.for_ is not None:
                head.append(f"for=({show_expr(v.for_)})")
            if v.tol is not None:
                head.append(f"tol=({show_expr(v.tol)})")
            if v.window is not None:
                kids.append(("window", v.window))
        elif is_dataclass(v) or isinstance(v, list):
            kids.append((f.name, v))
        else:
            head.append(f"{f.name}={v}")
    span = getattr(node, "span", NOWHERE)
    line = pad + " ".join(head) + (f" @{span}" if span is not NOWHERE and span.line else "")
    out = [line]
    for name, v in kids:
        out.append(f"{pad}  {name}:")
        out.append(dump(v, indent + 2))
    return "\n".join(out)
