"""Stage 3a, resolve: the symbol table and constant evaluation.

Walks the items in source order (no forward references), applies -D
overrides to params, and evaluates constant expressions to quantities with
dimensions. Errors: E101 unknown name, E102 dimension mismatch, E103 unknown
type, E104 value does not match the declared type, E105 division by zero,
E106 override of a const, E107 override of an unknown param, E110 a
comparison or bit index where a constant is needed, E215 duplicate name.

An item-level `if` (and one inside a role) is chosen here, on the params
declared before it, so a mode param picks wires, events and states before any
later stage runs; the symbol table holds the spec with the chosen branches.
"""
from dataclasses import replace
from fractions import Fraction as Fr

from . import ast as A
from .diag import NOWHERE, Errors, TseError
from .units import BASE_UNITS, DATA_TYPES, TYPE_DIM, Q, dimname

RELOPS = {"==": lambda a, b: a == b, "!=": lambda a, b: a != b, "<": lambda a, b: a < b,
          "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, ">=": lambda a, b: a >= b}
BIT_OPS = {"&": lambda a, b: a & b, "|": lambda a, b: a | b,
           "<<": lambda a, b: a << b, ">>": lambda a, b: a >> b}
FUNCTIONS = {"min": min, "max": max}


class Symbols:
    def __init__(self, spec):
        self.spec = spec
        self.params = {}        # name -> (type name, Q)
        self.units = {}         # name -> Q
        self.wires = {}         # name -> Wire
        self.events = {}        # name -> EventDef
        self.roles = []
        self.enums = {}         # name -> EnumDef
        self.frames = {}        # name -> Frame
        self.assists = {}       # name -> Assist
        self.triggers = []
        self.decl = {}          # name -> Ident where first declared

    def declare(self, ident, errs):
        first = self.decl.get(ident.text)
        if first is not None or ident.text in BASE_UNITS:
            where = f" at line {first.span.line}" if first else " as a built-in unit"
            errs.append(TseError("E215", f"'{ident.text}' is already declared{where}", ident.span))
            return False
        self.decl[ident.text] = ident
        return True

    def unit(self, ident):
        q = self.units.get(ident.text) or BASE_UNITS.get(ident.text)
        if q is None:
            raise TseError("E101", f"unknown unit '{ident.text}'", ident.span)
        return q

    def eval(self, e):
        """Constant expression to Q."""
        if isinstance(e, A.Num):
            q = Q(e.value, 0)
            if e.percent:
                return Q(e.value / 100, 0)
            return q.mul(self.unit(e.unit)) if e.unit else q
        if isinstance(e, A.Ref):
            if e.index is not None or e.attr is not None:
                raise TseError("E110", f"'{e.name.text}' is not a constant here", e.span)
            name = e.name.text
            if name in self.params:
                return self.params[name][1]
            if name in self.units or name in BASE_UNITS:
                return self.unit(e.name)
            raise TseError("E101", f"unknown name '{name}'", e.name.span)
        if isinstance(e, A.Call):
            fn = FUNCTIONS.get(e.name.text)
            if fn is None:
                raise TseError("E101", f"unknown function '{e.name.text}'", e.name.span)
            vals = [self.eval(a) for a in e.args]
            if not vals or any(v.dim != vals[0].dim for v in vals):
                raise TseError("E102", f"'{e.name.text}' needs one or more values of one dimension",
                               e.span)
            return Q(fn(v.v for v in vals), vals[0].dim)
        if isinstance(e, A.Tuple):
            raise TseError("E110", "a tuple is not a constant here", e.span)
        if isinstance(e, A.Unary):
            if e.op == "not":
                raise TseError("E110", "'not' needs a guard, not a constant", e.span)
            q = self.eval(e.operand)
            return Q(-q.v, q.dim)
        a, b = self.eval(e.left), self.eval(e.right)
        if e.op == "+":
            return a.add(b, e.span)
        if e.op == "-":
            return a.add(b, e.span, -1)
        if e.op == "*":
            return a.mul(b)
        if e.op == "/":
            return a.div(b, e.span)
        if e.op in BIT_OPS:
            if a.dim or b.dim or a.v.denominator != 1 or b.v.denominator != 1:
                raise TseError("E110", f"'{e.op}' needs whole numbers", e.span)
            return Q(Fr(BIT_OPS[e.op](int(a.v), int(b.v))), 0)
        raise TseError("E110", f"'{e.op}' needs a guard, not a constant", e.span)

    def time(self, e):
        return self.eval(e) if e is not None else None

    def truth(self, e):
        """A condition on constants: comparisons joined by and, or, not."""
        if isinstance(e, A.Unary) and e.op == "not":
            return not self.truth(e.operand)
        if isinstance(e, A.Binary) and e.op in ("and", "or"):
            a = self.truth(e.left)
            return (a and self.truth(e.right)) if e.op == "and" else (a or self.truth(e.right))
        if isinstance(e, A.Binary) and e.op in RELOPS:
            a, b = self.eval(e.left), self.eval(e.right)
            if a.dim != b.dim:
                raise TseError("E102", f"cannot compare {dimname(a.dim)} and {dimname(b.dim)}", e.span)
            return RELOPS[e.op](a.v, b.v)
        raise TseError("E111", "an item-level 'if' needs a comparison of params and constants",
                       e.span)


def type_dim(sym, ty, errs):
    name = ty.name.text
    if name in TYPE_DIM:
        return TYPE_DIM[name]
    if name in DATA_TYPES:
        if name in ("bits", "uint", "bytes", "words"):
            if ty.width is None:
                errs.append(TseError("E103", f"type '{name}' needs a width, as in {name}[8]",
                                     ty.name.span))
            else:
                sym.eval(ty.width)
                if ty.width_hi is not None:
                    if name != "bytes":
                        errs.append(TseError("E103", f"only bytes take a range, as in bytes[0..8]",
                                             ty.name.span))
                    sym.eval(ty.width_hi)
        return 0
    if name in sym.enums or name in sym.frames:
        return 0
    raise TseError("E103", f"unknown type '{name}'", ty.name.span)


def chosen(sym, node, errs):
    """The branch of an item-level or role-level if that its condition picks (None: neither)."""
    try:
        return node.then if sym.truth(node.cond) else node.orelse
    except TseError as e:
        errs.append(e)
        return None


def merge_role(sym, role, errs):
    """A role with its role-level ifs chosen (a copy, so the parsed tree stays as written)."""
    if not role.conds:
        return role
    out = replace(role, vars=list(role.vars), assumes=list(role.assumes), states=list(role.states),
                  clocks=list(role.clocks), conds=[])
    todo = list(role.conds)
    while todo:
        part = chosen(sym, todo.pop(0), errs)
        if part is not None:
            out.vars += part.vars
            out.assumes += part.assumes
            out.states += part.states
            out.clocks += part.clocks
            todo += part.conds
    return out


def resolve(spec, overrides=None):
    overrides = dict(overrides or {})
    sym, errs = Symbols(spec), []
    items, flat = list(spec.items), []
    while items:
        it = items.pop(0)
        if isinstance(it, A.ItemIf):
            items[:0] = chosen(sym, it, errs) or []
            continue
        if isinstance(it, A.Role):
            it = merge_role(sym, it, errs)
        flat.append(it)
        try:
            if isinstance(it, A.Param):
                if not sym.declare(it.name, errs):
                    continue
                dim = type_dim(sym, it.type, errs)
                q = sym.eval(it.value)
                name = it.name.text
                if name in overrides:
                    if it.const:
                        errs.append(TseError("E106", f"'{name}' is a const and cannot be "
                                             "overridden with -D", it.name.span))
                    else:
                        try:
                            q = Q(Fr(overrides[name]), dim)
                        except ValueError:
                            errs.append(TseError("E108", f"-D {name}={overrides[name]}: not a "
                                                 "number", NOWHERE))
                    del overrides[name]
                if q.dim != dim:
                    raise TseError("E104", f"{'const' if it.const else 'param'} '{name}' is "
                                   f"declared {it.type.name.text} but its value is a "
                                   f"{dimname(q.dim)}", it.name.span)
                sym.params[name] = (it.type.name.text, q)
            elif isinstance(it, A.UnitDef):
                if sym.declare(it.name, errs):
                    sym.units[it.name.text] = sym.eval(it.value)
            elif isinstance(it, A.Wire):
                if sym.declare(it.name, errs):
                    sym.wires[it.name.text] = it
            elif isinstance(it, A.EventDef):
                if sym.declare(it.name, errs):
                    sym.events[it.name.text] = it
            elif isinstance(it, A.Role):
                if sym.declare(it.name, errs):
                    sym.roles.append(it)
            elif isinstance(it, A.Datasheet):
                sym.declare(it.name, errs)
            elif isinstance(it, A.EnumDef):
                if sym.declare(it.name, errs):
                    sym.enums[it.name.text] = it
                    type_dim(sym, it.type, errs)
                    for m in it.members:
                        if sym.declare(m.name, errs):
                            sym.params[m.name.text] = (it.name.text, sym.eval(m.value))
            elif isinstance(it, A.Frame):
                if sym.declare(it.name, errs):
                    sym.frames[it.name.text] = it
            elif isinstance(it, A.Assist):
                if sym.declare(it.name, errs):
                    sym.assists[it.name.text] = it
            elif isinstance(it, A.Trigger):
                if sym.declare(it.name, errs):
                    sym.triggers.append(it)
        except TseError as e:
            errs.append(e)
    for name in overrides:
        errs.append(TseError("E107", f"-D {name}: no param named '{name}'", NOWHERE))
    if errs:
        raise Errors(errs)
    sym.spec = spec if flat == spec.items else replace(spec, items=flat)
    return sym
