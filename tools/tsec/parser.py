"""Stage 2, parser: tokens to syntax tree.

Recursive descent, one method per grammar rule, named after the rule. The
grammar is LL(1) except `shift`, where the token after the first name
(`->` or not) tells an input shift from an output shift.

Error recovery is panic mode: a failed item, edge or statement is recorded,
the parser skips to the next `;` (or the `}` closing the current block) and
carries on, so one run reports several mistakes, capped at MAX_ERRORS.
"""
from . import ast as A
from .diag import MAX_ERRORS, Errors, TseError
from .lexer import lex

# Names that may follow an expression; any other name right after a number is its unit.
FOLLOWS_EXPR = {"for", "within", "after", "each", "when", "and", "or", "do", "timeout",
                "else", "lsb", "msb", "expect", "idle", "sample_delay", "filter",
                "push_pull", "open_drain", "pull_up", "then"}
RELOPS = ("==", "!=", "<", "<=", ">", ">=")
WIRE_FLAGS = ("push_pull", "open_drain", "pull_up")
WIRE_VALUES = ("idle", "sample_delay", "filter")
PIN_EVENTS = ("rise", "fall", "edge", "taken")
UNSUPPORTED = ("enum", "frame", "assist", "trigger", "clock", "send", "recv")


class Parser:
    def __init__(self, toks):
        self.toks, self.i, self.errors = toks, 0, []

    # ------------------------------------------------------------ token helpers
    def peek(self, k=0):
        return self.toks[min(self.i + k, len(self.toks) - 1)]

    def at(self, *texts):
        tok = self.peek()
        return tok.kind != "num" and tok.text in texts

    def take(self):
        tok = self.peek()
        if tok.kind != "eof":
            self.i += 1
        return tok

    def accept(self, text):
        if self.at(text):
            return self.take()
        return None

    def expect(self, text):
        tok = self.take()
        if tok.text != text or tok.kind == "num":
            raise TseError("E010", f"expected '{text}' but found {tok}", tok.span)
        return tok

    def ident(self, what="a name"):
        tok = self.take()
        if tok.kind != "name":
            raise TseError("E011", f"expected {what} but found {tok}", tok.span)
        return A.Ident(tok.text, tok.span)

    def one_of(self, code, choices, what):
        tok = self.take()
        if tok.text not in choices:
            raise TseError(code, f"expected {what} but found {tok}", tok.span)
        return tok.text

    # ------------------------------------------------------------ recovery
    def record(self, err):
        self.errors.append(err)
        if len(self.errors) >= MAX_ERRORS:
            raise Errors(self.errors)

    def sync(self):
        """Skip to just after the next ';' or to the '}' closing this block."""
        depth = 0
        while self.peek().kind != "eof":
            tok = self.peek()
            if tok.text == "}" and depth == 0:
                return
            self.take()
            if tok.text == "{":
                depth += 1
            elif tok.text == "}":
                depth -= 1
                if depth == 0 and not self.at("->", "within", "else", ";"):
                    return
            elif tok.text == ";" and depth == 0:
                return

    def many(self, parse_one, out):
        """Parse items until '}' with recovery; the caller consumes the brace."""
        while not self.at("}") and self.peek().kind != "eof":
            start = self.i
            try:
                node = parse_one()
                if node is not None:
                    out.append(node)
            except TseError as e:
                self.record(e)
                if self.i == start:
                    self.take()
                self.sync()
        return out

    # ------------------------------------------------------------ spec, items
    def spec(self):
        self.expect("protocol")
        name = self.ident()
        spec = A.Spec(name, span=name.span)
        self.expect("{")
        self.many(self.item, spec.items)
        self.expect("}")
        tok = self.peek()
        if tok.kind != "eof":
            raise TseError("E015", "text after the end of the protocol", tok.span)
        return spec

    def item(self):
        tok = self.peek()
        handlers = {"param": self.param, "const": self.param, "unit": self.unitdef,
                    "wire": self.wire, "event": self.eventdef, "datasheet": self.datasheet,
                    "role": self.role}
        if tok.kind == "name" and tok.text in handlers:
            return handlers[tok.text]()
        if tok.text in UNSUPPORTED:
            raise TseError("E023", f"'{tok.text}' is not supported by this version of tsec", tok.span)
        raise TseError("E014", f"expected param, const, unit, wire, event, datasheet or role, "
                       f"found {tok}", tok.span)

    def param(self):
        const = self.take().text == "const"
        name = self.ident()
        self.expect(":")
        ty = self.type_ref()
        self.expect("=")
        value = self.expr()
        self.expect(";")
        return A.Param(name, ty, value, const, span=name.span)

    def type_ref(self):
        name = self.ident("a type")
        width = None
        if self.accept("["):
            width = self.expr()
            self.expect("]")
        return A.TypeRef(name, width, span=name.span)

    def unitdef(self):
        self.take()
        name = self.ident()
        self.expect("=")
        value = self.expr()
        self.expect(";")
        return A.UnitDef(name, value, span=name.span)

    def wire(self):
        self.take()
        name = self.ident()
        self.expect(":")
        direction = self.one_of("E022", ("in", "out", "inout"), "in, out or inout")
        attrs = []
        while not self.at(";"):
            a = self.ident("a wire attribute")
            if a.text in WIRE_FLAGS:
                attrs.append(A.WireAttr(a))
            elif a.text in WIRE_VALUES:
                attrs.append(A.WireAttr(a, self.expr()))
            else:
                raise TseError("E016", f"unknown wire attribute '{a.text}'", a.span)
        self.expect(";")
        return A.Wire(name, direction, attrs, span=name.span)

    def eventdef(self):
        self.take()
        name = self.ident()
        self.expect("=")
        ev = self.event()
        guard = self.expr() if self.accept("when") else None
        self.expect(";")
        return A.EventDef(name, ev, guard, span=name.span)

    def datasheet(self):
        self.take()
        name = self.ident()
        self.expect("{")
        rules = self.many(self.rule, [])
        self.expect("}")
        return A.Datasheet(name, rules, span=name.span)

    def rule(self):
        self.expect("rule")
        name = self.ident()
        self.expect(":")
        a = self.event()
        self.expect("->")
        b = self.event()
        relop = self.one_of("E024", RELOPS, "a comparison")
        bound = self.expr()
        guard = self.expr() if self.accept("when") else None
        self.expect(";")
        return A.Rule(name, a, b, relop, bound, guard, span=name.span)

    # ------------------------------------------------------------ roles, states
    def role(self):
        self.take()
        name = self.ident()
        self.expect(":")
        kind = self.one_of("E021", ("controller", "target", "monitor"),
                           "controller, target or monitor")
        self.expect("uses")
        uses = [self.ident("a wire name")]
        while self.accept(","):
            uses.append(self.ident("a wire name"))
        lane = self.expr() if self.accept("lane") else None
        role = A.Role(name, kind, uses, lane, span=name.span)
        self.expect("{")
        self.many(lambda: self.role_item(role), [])
        self.expect("}")
        return role

    def role_item(self, role):
        tok = self.peek()
        if tok.text == "var":
            role.vars.append(self.var())
        elif tok.text == "assume":
            role.assumes.append(self.assume())
        elif tok.text in ("state", "initial"):
            role.states.append(self.state())
        elif tok.text in UNSUPPORTED:
            raise TseError("E023", f"'{tok.text}' is not supported by this version of tsec", tok.span)
        else:
            raise TseError("E017", f"expected var, assume or state, found {tok}", tok.span)

    def var(self):
        self.take()
        name = self.ident()
        self.expect(":")
        ty = self.type_ref()
        init = self.expr() if self.accept("=") else None
        self.expect(";")
        return A.Var(name, ty, init, span=name.span)

    def assume(self):
        self.take()
        name = self.ident()
        self.expect(":")
        a = self.event()
        node = A.Assume(name, a, span=name.span)
        if self.accept("->"):
            node.b = self.event()
            node.relop = self.one_of("E024", RELOPS, "a comparison")
            node.bound = self.expr()
        self.expect(";")
        return node

    def state(self):
        initial = self.accept("initial") is not None
        self.expect("state")
        name = self.ident()
        inv = self.expr() if self.accept("inv") else None
        self.expect("{")
        edges = self.many(self.edge, [])
        self.expect("}")
        return A.State(name, edges, initial, inv, span=name.span)

    def edge(self):
        tok = self.take()
        if tok.text == "on":
            ev = self.event()
            guard = self.expr() if self.accept("when") else None
            body = self.block() if self.accept("do") else None
            self.expect("->")
            node = A.OnEdge(ev, guard, body, self.ident("a state name"), span=tok.span)
        elif tok.text == "after":
            guard = self.expr()
            body = self.block() if self.accept("do") else None
            self.expect("->")
            node = A.AfterEdge(guard, body, self.ident("a state name"), span=tok.span)
        elif tok.text == "seq":
            body = self.block()
            self.expect("->")
            node = A.SeqEdge(body, self.ident("a state name"), span=tok.span)
            if self.accept("abort"):
                self.expect("on")
                node.abort_on = self.event()
                self.expect("->")
                node.abort_to = self.ident("a state name")
        elif tok.text == "respond":
            name = self.ident()
            self.expect(":")
            self.expect("on")
            ev = self.event()
            guard = self.expr() if self.accept("when") else None
            body = self.block()
            self.expect("within")
            lo, hi = self.bounds()
            self.expect("->")
            node = A.RespondEdge(name, ev, guard, body, lo, hi, self.ident("a state name"),
                                 span=tok.span)
        else:
            raise TseError("E018", f"expected on, after, seq or respond, found {tok}", tok.span)
        self.expect(";")
        return node

    def bounds(self):
        self.expect("[")
        lo = self.expr()
        self.expect(",")
        hi = self.expr()
        self.expect("]")
        return lo, hi

    def event(self):
        tok = self.take()
        if tok.kind != "name":
            raise TseError("E019", f"expected an event but found {tok}", tok.span)
        if tok.text in PIN_EVENTS:
            self.expect("(")
            wire = self.ident("a wire name")
            self.expect(")")
            return A.Event(tok.text, wire, span=tok.span)
        ev = A.Event("named", A.Ident(tok.text, tok.span), span=tok.span)
        if self.accept("."):
            ev.attr = self.ident()
        if self.accept("("):
            if not self.at(")"):
                ev.args.append(self.expr())
                while self.accept(","):
                    ev.args.append(self.expr())
            self.expect(")")
        return ev

    # ------------------------------------------------------------ statements
    def block(self):
        self.expect("{")
        body = self.many(self.stmt, [])
        self.expect("}")
        return body

    def timing(self, t=None):
        t = t or A.Timing()
        if self.accept("within"):
            lo, hi = self.bounds()
            span = self.expect("after").span
            t.window = A.Window(lo, hi, self.event(), span=span)
        if self.accept("for"):
            t.for_ = self.expr()
        if self.accept("~"):
            t.tol = self.expr()
        return t

    def pins(self):
        if self.accept("("):
            wires = [self.ident("a wire name")]
            while self.accept(","):
                wires.append(self.ident("a wire name"))
            self.expect(")")
            return wires
        return [self.ident("a wire name")]

    def stmt(self):
        tok = self.peek()
        if tok.kind != "name":
            self.take()
            raise TseError("E020", f"expected a statement but found {tok}", tok.span)
        method = getattr(self, "stmt_" + tok.text, None)
        if method is None:
            self.take()
            if tok.text in UNSUPPORTED:
                raise TseError("E023", f"'{tok.text}' is not supported by this version of tsec",
                               tok.span)
            raise TseError("E020", f"unknown statement '{tok.text}'", tok.span)
        self.take()
        node = method(tok.span)
        if not isinstance(node, (A.Repeat, A.If)):
            self.expect(";")
        return node

    def stmt_drive(self, span):
        wire = self.ident("a wire name")
        self.expect("=")
        value = self.expr()
        return A.Drive(wire, value, self.timing(), span=span)

    def stmt_release(self, span):
        return A.Release(self.ident("a wire name"), self.timing(), span=span)

    def stmt_shift(self, span):
        if self.peek(1).text == "->" or self.at("("):      # shift WIRE -> VAR lsb N
            wires = self.pins()
            self.expect("->")
            var = self.ident("a variable name")
            order = self.one_of("E025", ("lsb", "msb"), "lsb or msb")
            node = A.Shift(False, var, order, self.expr(), wires, span=span)
        else:                                               # shift VAR lsb N -> WIRE
            var = self.ident("a variable name")
            order = self.one_of("E025", ("lsb", "msb"), "lsb or msb")
            count = self.expr()
            self.expect("->")
            node = A.Shift(True, var, order, count, self.pins(), span=span)
        if self.accept("each"):
            node.each = self.expr()
            if self.accept("~"):
                node.timing.tol = self.expr()
        else:
            self.timing(node.timing)
        return node

    def stmt_sample(self, span):
        wire = self.ident("a wire name")
        expect = self.expr() if self.accept("expect") else None
        return A.Sample(wire, expect, self.timing(), span=span)

    def stmt_await(self, span):
        ev = self.event()
        guard = self.expr() if self.accept("when") else None
        timeout = self.expr() if self.accept("timeout") else None
        return A.Await(ev, guard, timeout, span=span)

    def stmt_repeat(self, span):
        count = self.expr()
        return A.Repeat(count, self.block(), span=span)

    def stmt_if(self, span):
        guard = self.expr()
        then = self.block()
        orelse = self.block() if self.accept("else") else None
        return A.If(guard, then, orelse, span=span)

    def stmt_wait(self, span):
        return A.Wait(self.expr(), span=span)

    def stmt_push(self, span):
        self.expect("host")
        return A.HostIO(False, self.ident("a variable name"), span=span)

    def stmt_pull(self, span):
        self.expect("host")
        return A.HostIO(True, self.ident("a variable name"), span=span)

    def stmt_emit(self, span):
        name = self.ident()
        args = []
        if self.accept("("):
            args.append(self.expr())
            while self.accept(","):
                args.append(self.expr())
            self.expect(")")
        return A.Emit(name, args, span=span)

    def stmt_reset(self, span):
        names = [self.ident()]
        while self.accept(","):
            names.append(self.ident())
        return A.Reset(names, span=span)

    def stmt_set(self, span):
        name = self.ident()
        self.expect("=")
        return A.Set(name, self.expr(), span=span)

    # ------------------------------------------------------------ expressions
    # expr := and { "or" and } ; and := not { "and" not } ; not := ["not"] rel
    # rel := sum [relop sum] ; sum := term { (+ - |) term } ; term := unary { (* / & << >>) unary }
    def expr(self):
        left = self.and_expr()
        while self.at("or"):
            op = self.take()
            left = A.Binary("or", left, self.and_expr(), span=op.span)
        return left

    def and_expr(self):
        left = self.not_expr()
        while self.at("and"):
            op = self.take()
            left = A.Binary("and", left, self.not_expr(), span=op.span)
        return left

    def not_expr(self):
        if self.at("not"):
            op = self.take()
            return A.Unary("not", self.not_expr(), span=op.span)
        return self.rel()

    def rel(self):
        left = self.sum()
        if self.peek().kind == "sym" and self.at(*RELOPS):
            op = self.take()
            left = A.Binary(op.text, left, self.sum(), span=op.span)
        return left

    def sum(self):
        left = self.term()
        while self.peek().kind == "sym" and self.at("+", "-", "|"):
            op = self.take()
            left = A.Binary(op.text, left, self.term(), span=op.span)
        return left

    def term(self):
        left = self.unary()
        while self.peek().kind == "sym" and self.at("*", "/", "&", "<<", ">>"):
            op = self.take()
            left = A.Binary(op.text, left, self.unary(), span=op.span)
        return left

    def unary(self):
        if self.peek().kind == "sym" and self.at("-"):
            op = self.take()
            return A.Unary("-", self.unary(), span=op.span)
        return self.primary()

    def primary(self):
        tok = self.take()
        if tok.kind == "num":
            node = A.Num(tok.value, text=tok.text, span=tok.span)
            if self.peek().kind == "sym" and self.at("%"):
                self.take()
                node.percent = True
            elif self.peek().kind == "name" and self.peek().text not in FOLLOWS_EXPR:
                u = self.take()
                node.unit = A.Ident(u.text, u.span)
            return node
        if tok.kind == "name":
            node = A.Ref(A.Ident(tok.text, tok.span), span=tok.span)
            if self.accept("["):
                node.index = self.expr()
                if self.accept(":"):
                    node.index_lo = self.expr()
                self.expect("]")
            if self.accept("."):
                node.attr = self.ident()
            return node
        if tok.text == "(":
            node = self.expr()
            self.expect(")")
            return node
        raise TseError("E013", f"expected an expression but found {tok}", tok.span)


def parse(src):
    """Source text to Spec; raises Errors with every syntax error found."""
    try:
        toks = lex(src)
    except TseError as e:
        raise Errors([e]) from None
    p = Parser(toks)
    try:
        spec = p.spec()
    except TseError as e:
        p.errors.append(e)
        spec = None
    if p.errors:
        raise Errors(p.errors)
    return spec
