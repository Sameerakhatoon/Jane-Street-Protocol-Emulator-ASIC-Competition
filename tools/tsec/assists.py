# SPDX-License-Identifier: Apache-2.0
"""Stage 6b, assist configuration: `assist` items to lane register values.

One function per assist kind turns the declaration into register values
through tools.tseasm.regs.pack, which range-checks every field against the
register table. check() runs the same functions during semantic checks, so a
bad parameter is E230 (out of range), E231 (CRC check value wrong) or E232
(missing or unknown parameter) before anything is compiled; R303, R304,
R318 and R319 are the per-lane limits checked when a role is built.

The reference CRC is the usual catalogue model (width, poly, init, refin,
refout, xorout) run bit by bit; `check` is its value over "123456789".
"""
import math
from fractions import Fraction as Fr

from . import ast as A
from ..tseasm import regs
from .diag import TseError

PARAMS = {    # kind: {name: default}; REQUIRED marks the ones without a default
    "crc": {"width": None, "poly": None, "init": 0, "refin": "false", "refout": "false",
            "xorout": 0, "check": "none"},
    "stuffer": {"run": None, "value": None},
    "nrzi": {"pins": "none", "transition": 0, "idle": "none"},
    "manchester": {"pins": "none", "convention": "ieee", "idle": "none"},
    "dpll": {"period": None, "sample": Fr(1, 2), "sjw": 0, "dual_edge": "false",
             "resync": "true", "edges": "both"},
    "timer": {"period": None, "event0": "true"},
    "rule": {"clock": "bits", "tick_delay": 0, "pattern": None, "mask": 0xFFFF, "pos": "none",
             "any": "false", "resp": 0, "len": 1, "offset": 0, "cond": "none",
             "drive": "true", "abort": "false"},
    "arbitration": {},
}
WORDS = {"true", "false", "none", "either", "ieee", "thomas", "rise", "fall", "both", "bits",
         "crc_good", "crc_bad", "tx_idle", "J", "K"}
CATALOGUE = {(16, 0x8005, 0xFFFF, 1, 1, 0xFFFF): "CRC-16/USB", (5, 0x05, 0x1F, 1, 1, 0x1F): "CRC-5/USB",
             (15, 0x4599, 0, 0, 0, 0): "CRC-15/CAN",
             (32, 0x04C11DB7, 0xFFFFFFFF, 1, 1, 0xFFFFFFFF): "CRC-32"}
SINGLE = {"crc": "a CRC", "stuffer": "a stuffer", "nrzi": "a line codec", "manchester": "a line codec",
          "dpll": "a DPLL", "timer": "a lane timer", "arbitration": "an arbitration check"}


def reflect(v, w):
    r = 0
    for i in range(w):
        r = (r << 1) | ((v >> i) & 1)
    return r


def crc_bits(bits, width, poly, reg):
    mask = (1 << width) - 1
    for bit in bits:
        fb = ((reg >> (width - 1)) & 1) ^ bit
        reg = (reg << 1) & mask
        if fb:
            reg ^= poly
    return reg


def message_bits(data, refin):
    out = []
    for byte in data:
        b = reflect(byte, 8) if refin else byte
        out += [(b >> i) & 1 for i in range(7, -1, -1)]
    return out


def crc(data, width, poly, init, refin, refout, xorout):
    reg = crc_bits(message_bits(data, refin), width, poly, init)
    return (reflect(reg, width) if refout else reg) ^ xorout


def residue(width, poly, init, refin, refout, xorout):
    """Register contents after a valid codeword ("123456789" plus its CRC, as sent)."""
    data = b"123456789"
    c = crc(data, width, poly, init, refin, refout, xorout)
    order = range(width) if refout else range(width - 1, -1, -1)
    return crc_bits(message_bits(data, refin) + [(c >> k) & 1 for k in order], width, poly, init)


class Params:
    """The parameters of one assist, evaluated, with defaults; errors are E230 and E232."""

    def __init__(self, sym, a, fclk=None):
        self.sym, self.a, self.fclk = sym, a, fclk
        spec = PARAMS[a.kind]
        for p in a.params:
            if p.name.text not in spec:
                raise TseError("E232", f"assist '{a.name.text}': {a.kind} has no parameter "
                               f"'{p.name.text}' (it has {', '.join(spec)})", p.name.span)
        for name, default in spec.items():
            if default is None and a.get(name) is None:
                raise TseError("E232", f"assist '{a.name.text}': {a.kind} needs '{name}'", a.name.span)

    def node(self, name):
        p = self.a.get(name)
        return p.value if p is not None else None

    def span(self, name):
        p = self.a.get(name)
        return p.value.span if p is not None else self.a.name.span

    def word(self, name, choices):
        n = self.node(name)
        v = PARAMS[self.a.kind][name] if n is None else None
        if n is not None:
            if isinstance(n, A.Ref) and n.name.text in WORDS:
                v = n.name.text
            elif isinstance(n, A.Num) and n.value in (0, 1) and not n.unit:
                v = str(int(n.value))
        if v not in choices:
            raise TseError("E230", f"assist '{self.a.name.text}': {name} must be one of "
                           f"{', '.join(choices)}", self.span(name))
        return v

    def flag(self, name):
        return self.word(name, ("true", "false", "0", "1")) in ("true", "1")

    def number(self, name, dim=0, lo=None, hi=None, whole=True):
        n = self.node(name)
        if n is None:
            v = Fr(PARAMS[self.a.kind][name])
        else:
            q = self.sym.eval(n)
            if q.dim != dim:
                kind = {0: "a number", 1: "a time", -1: "a frequency"}[dim]
                raise TseError("E230", f"assist '{self.a.name.text}': {name} must be {kind}",
                               self.span(name))
            v = q.v
        if whole and v.denominator != 1:
            raise TseError("E230", f"assist '{self.a.name.text}': {name} must be a whole number",
                           self.span(name))
        if (lo is not None and v < lo) or (hi is not None and v > hi):
            raise TseError("E230", f"assist '{self.a.name.text}': {name} {show(v)} is outside "
                           f"{show(lo)}..{show(hi)}", self.span(name))
        return int(v) if whole else v

    def ticks(self, name, lo=None, hi=None):
        n = self.node(name)
        if n is None:
            return Fr(PARAMS[self.a.kind][name])
        q = self.sym.eval(n)
        t = q.v * self.fclk if q.dim == 1 else q.v if q.dim == 0 else None
        if t is None:
            raise TseError("E230", f"assist '{self.a.name.text}': {name} must be a time",
                           self.span(name))
        if (lo is not None and t < lo) or (hi is not None and t > hi):
            raise TseError("E230", f"assist '{self.a.name.text}': {name} is {float(t):.2f} ticks, "
                           f"outside {show(lo)}..{show(hi)}", self.span(name))
        return t

    def pins(self, name):
        n = self.node(name)
        items = n.items if isinstance(n, A.Tuple) else [] if n is None else [n]
        out = []
        for it in items:
            if not (isinstance(it, A.Ref) and it.name.text in self.sym.wires):
                raise TseError("E230", f"assist '{self.a.name.text}': {name} must name wires",
                               it.span)
            out.append(it.name.text)
        if isinstance(n, A.Ref) and n.name.text == "none":
            return []
        return out


def show(v):
    if v is None:
        return "any"
    return str(int(v)) if Fr(v).denominator == 1 else f"{float(v):g}"


def pack(a, name, **fields):
    try:
        return regs.pack(name, **fields)
    except regs.RegError as e:
        raise TseError("E230", f"assist '{a.name.text}': {e}", a.name.span) from None


# ---------------------------------------------------------------- one function per kind
def crc_regs(p):
    a = p.a
    w = p.number("width", lo=5, hi=32)
    poly = p.number("poly", lo=0, hi=(1 << w) - 1)
    init = p.number("init", lo=0, hi=(1 << w) - 1)
    xorout = p.number("xorout", lo=0, hi=(1 << w) - 1)
    refin, refout = p.flag("refin"), p.flag("refout")
    got = crc(b"123456789", w, poly, init, refin, refout, xorout)
    checks = {"crc_check": f"0x{got:0{(w + 3) // 4}X}"}
    name = CATALOGUE.get((w, poly, init, int(refin), int(refout), xorout))
    if name:
        checks["catalogue"] = name
    if p.node("check") is not None:
        want = p.number("check", lo=0, hi=(1 << w) - 1)
        if want != got:
            raise TseError("E231", f"assist '{a.name.text}': parameters give 0x{got:X}, check says "
                           f"0x{want:X}{crc_hint(w, poly, init, refin, refout, xorout, want)}",
                           p.span("check"))
    res = residue(w, poly, init, refin, refout, xorout)
    checks["residue"] = f"0x{(reflect(res, w) if refout else res):0{(w + 3) // 4}X}"
    shift = 32 - w
    checks["result"] = (f"CRC_RESULT bits {w - 1} to 0 (reflected out)" if refout else
                        f"CRC_RESULT bits 31 to {shift} (left-aligned)")
    return {"CRC_POLY": pack(a, "CRC_POLY", poly=poly << shift),
            "CRC_INIT": pack(a, "CRC_INIT", init=init << shift),
            "CRC_XOR": pack(a, "CRC_XOR", xorout=(reflect(xorout, w) if refout else xorout) << shift),
            "CRC_CHECK": pack(a, "CRC_CHECK", check=res << shift),
            "CRC_CTL": pack(a, "CRC_CTL", wm1=w - 1, refin=int(refin), refout=int(refout))}, checks


def crc_hint(w, poly, init, refin, refout, xorout, want):
    """The classic mistakes: a reflection flag missing, or the polynomial typed reflected."""
    tries = [(f"is refin {'set by mistake' if refin else 'missing'}?",
              (w, poly, init, not refin, refout, xorout)),
             (f"is refout {'set by mistake' if refout else 'missing'}?",
              (w, poly, init, refin, not refout, xorout)),
             ("are refin and refout missing?", (w, poly, init, not refin, not refout, xorout)),
             ("is the polynomial typed reflected?", (w, reflect(poly, w), init, refin, refout, xorout))]
    for text, args in tries:
        if crc(b"123456789", *args) == want:
            return f" ({text})"
    return ""


def stuffer_regs(p):
    run = p.number("run", lo=1, hi=15)
    v = p.word("value", ("0", "1", "either"))
    return {"STUFF_CFG": pack(p.a, "STUFF_CFG", n=run, ones=int(v in ("1", "either")),
                              zeros=int(v in ("0", "either")))}, {}


def nrzi_regs(p):
    p.pins("pins")
    t = p.number("transition", lo=0, hi=1)
    return {"CODEC_CFG": pack(p.a, "CODEC_CFG", mode="NRZI", t_on1=t)}, {}


def manchester_regs(p):
    p.pins("pins")
    conv = p.word("convention", ("ieee", "thomas"))
    return {"CODEC_CFG": pack(p.a, "CODEC_CFG", mode="MANCHESTER", inv=int(conv == "thomas"))}, {}


def dpll_regs(p):
    period = p.ticks("period", lo=2, hi=Fr(65535))
    sample = p.number("sample", lo=0, hi=1, whole=False)
    if not 0 < sample < 1:
        raise TseError("E230", f"assist '{p.a.name.text}': the sample point must be inside the "
                       "period", p.span("sample"))
    sjw = p.ticks("sjw", lo=0)
    if sjw > min(sample, 1 - sample) * period:
        raise TseError("E230", f"assist '{p.a.name.text}': sjw {float(sjw):.2f} ticks is larger than "
                       f"a phase segment ({float(min(sample, 1 - sample) * period):.2f} ticks)",
                       p.span("sjw"))
    edges = p.word("edges", ("rise", "fall", "both"))
    q16 = round(period * 16)
    return {"DPLL_PERIOD": pack(p.a, "DPLL_PERIOD", period=q16),
            "DPLL_SPT": pack(p.a, "DPLL_SPT", spt=round(period * sample * 16)),
            "DPLL_SJW": pack(p.a, "DPLL_SJW", sjw=round(sjw * 16)),
            "DPLL_CTL": pack(p.a, "DPLL_CTL", resync=int(p.flag("resync")), dual=int(p.flag("dual_edge")),
                             rise=int(edges in ("rise", "both")), fall=int(edges in ("fall", "both")))}, \
        {"dpll_period_ticks": f"{q16}/16 = {q16 / 16:.4f}",
         "dpll_error_ticks": f"{float(Fr(q16, 16) - period):+.4f}"}


def timer_regs(p):
    period = p.ticks("period", lo=1, hi=Fr(65536))
    ev0 = int(p.flag("event0"))
    if period.denominator == 1:
        ctl = pack(p.a, "TIMER_CTL", reload=int(period) - 1, on_tick=0, en=1, ev0_sel=ev0)
        return {"TIMER_INC": 0, "TIMER_CTL": ctl}, {"timer_period_ticks": str(int(period))}
    inc = round(Fr(1 << 24) / period)
    if not 0 < inc < 1 << 24:
        raise TseError("E230", f"assist '{p.a.name.text}': period {float(period):.2f} ticks needs "
                       "an increment outside the 24-bit field", p.span("period"))
    return {"TIMER_INC": pack(p.a, "TIMER_INC", inc=inc),
            "TIMER_CTL": pack(p.a, "TIMER_CTL", reload=0, on_tick=1, en=1, ev0_sel=ev0)}, \
        {"timer_period_ticks": f"{float(Fr(1 << 24, inc)):.4f} (wanted {float(period):.4f})"}


def rule_regs(p, index, vpin):
    a = p.a
    n = p.node("clock")
    if isinstance(n, A.Tuple) and len(n.items) == 2 and isinstance(n.items[0], A.Ref) \
            and n.items[0].name.text in p.sym.wires and isinstance(n.items[1], A.Ref) \
            and n.items[1].name.text in ("rise", "fall"):
        pin, edge = n.items[0].name.text, n.items[1].name.text
    elif n is None or (isinstance(n, A.Ref) and n.name.text == "bits"):
        pin, edge = None, None
    else:
        raise TseError("E230", f"assist '{a.name.text}': clock must be 'bits' or (wire, rise|fall)",
                       p.span("clock"))
    delay = p.ticks("tick_delay", lo=0, hi=255)
    if delay.denominator != 1:
        delay = Fr(math.ceil(delay))
    if pin is None and delay:
        raise TseError("E230", f"assist '{a.name.text}': tick_delay needs a pin clock, as in "
                       "clock = (scl, fall)", p.span("tick_delay"))
    length, offset = p.number("len", lo=1, hi=16), p.number("offset", lo=0, hi=15)
    any_pos = p.flag("any")
    pos = None if p.node("pos") is None else p.number("pos", lo=0, hi=254)
    if pos is None and not any_pos:
        raise TseError("E232", f"assist '{a.name.text}': rule needs 'pos' or 'any = true'", a.name.span)
    cond = p.word("cond", ("none", "crc_good", "crc_bad", "tx_idle")).upper()
    r = f"RULE{index}"
    out = {f"{r}_MATCH": pack(a, f"{r}_MATCH", pattern=p.number("pattern", lo=0, hi=0xFFFF),
                              mask=p.number("mask", lo=0, hi=0xFFFF)),
           f"{r}_RESP_POS": pack(a, f"{r}_RESP_POS", resp=p.number("resp", lo=0, hi=0xFFFF),
                                 pos=pos or 0, any=int(any_pos)),
           f"{r}_CTL": pack(a, f"{r}_CTL", rlen_m1=length - 1, offset=offset, cond=cond,
                            drive=int(p.flag("drive")), abort=int(p.flag("abort")), en=1)}
    clk = {"src": 0} if pin is None else {"src": 1, "clk_pin": vpin(pin), "edge": int(edge == "fall"),
                                         "tick_delay": int(delay)}
    info = {"pin": pin, "edge": edge, "tick_delay": int(delay), "pos": pos, "offset": offset}
    return out, pack(a, "RULE_CLK", **clk), info


def arbitration_regs(p):
    """Arbitration loss: the lane's TX shift register sends recessive (the idle level) and the
    DPLL's sample point reads dominant; the lane then stops sending and raises its abort event
    (`NAME.lost`). Only LANE_CFG.arb_en, set by the lane configuration."""
    return {}, {"arb_en": 1}


KINDS = {"crc": crc_regs, "stuffer": stuffer_regs, "nrzi": nrzi_regs, "manchester": manchester_regs,
         "dpll": dpll_regs, "timer": timer_regs, "arbitration": arbitration_regs}


def check(sym, errs, fclk=Fr(60_000_000)):
    """E230 to E232 for every assist, independent of the lane it lands on."""
    for a in sym.assists.values():
        try:
            p = Params(sym, a, fclk)
            if a.kind == "rule":
                rule_regs(p, 0, lambda w: 0)
            else:
                KINDS[a.kind](p)
        except TseError as e:
            errs.append(e)


def for_role(sym, role):
    """The assists a role's lane carries: all of them in a one-role spec, else the ones it names."""
    if len(sym.roles) == 1:
        return list(sym.assists.values())
    named = set()

    def walk(x):
        if isinstance(x, A.Event) and x.kind == "named":
            named.add(x.name.text)
        elif isinstance(x, (A.Send, A.Recv)) and x.name.text in sym.frames:
            for f in sym.frames[x.name.text].fields:
                named.update(at.args[0].text for at in f.attrs if at.kind == "crc")
        if isinstance(x, list):
            for y in x:
                walk(y)
        elif hasattr(x, "__dataclass_fields__"):
            for k in x.__dataclass_fields__:
                if k != "span":
                    walk(getattr(x, k))
    walk(role.states)
    return [a for a in sym.assists.values() if a.name.text in named]


def min_period(sym, fclk, pin):
    """Shortest period of a clock wire in ticks from the datasheet, or None if unbounded."""
    sep = {}
    for d in sym.spec.of(A.Datasheet):
        for r in d.rules:
            if r.guard is None and r.relop in (">=", ">") and r.a.kind in ("rise", "fall") \
                    and r.a.name.text == pin and r.b.kind in ("rise", "fall") and r.b.name.text == pin:
                sep[(r.a.kind, r.b.kind)] = sym.eval(r.bound).v * fclk
    if ("rise", "fall") in sep and ("fall", "rise") in sep:
        return sep[("rise", "fall")] + sep[("fall", "rise")]
    same = [sep[k] for k in (("rise", "rise"), ("fall", "fall")) if k in sep]
    return min(same) if same else None


def lane_config(sym, role, fclk, vpin):
    """(register values, checks) of the assists on one role's lane, with the lane limits."""
    out, checks, seen, rules = {}, {}, {}, []
    for a in for_role(sym, role):
        p = Params(sym, a, fclk)
        if a.kind == "rule":
            rules.append((a, p))
            continue
        unit = SINGLE[a.kind]
        if unit in seen:
            raise TseError("R304", f"assist resources exhausted: role '{role.name.text}' needs "
                           f"{unit} for '{seen[unit]}' and for '{a.name.text}', a lane has one",
                           a.name.span)
        seen[unit] = a.name.text
        values, info = KINDS[a.kind](p)
        if a.kind == "arbitration" and "a DPLL" not in seen and not any(
                b.kind == "dpll" for b in for_role(sym, role)):
            raise TseError("R304", f"assist '{a.name.text}': arbitration needs a dpll assist on the "
                           "same lane (its sample point decides a lost bit)", a.name.span)
        out.update(values)
        checks.update({f"{a.name.text}.{k}": v for k, v in info.items()})
    if len(rules) > 2:
        raise TseError("R303", f"role '{role.name.text}' needs {len(rules)} response rules; a lane "
                       "has 2", rules[2][0].name.span)
    clocks = set()
    for i, (a, p) in enumerate(rules):
        values, clk, info = rule_regs(p, i, vpin)
        out.update(values)
        clocks.add(clk)
        if info["pos"] is not None and info["pos"] + info["offset"] > 254:
            raise TseError("R318", f"rule '{a.name.text}': pos + offset = "
                           f"{info['pos'] + info['offset']} > 254; the frame bit counter "
                           "saturates at 255", a.name.span,
                           fields=[("fix", "respond earlier in the frame, or re-base the counter "
                                    "with a frame reset before the rule's position")])
        if info["pin"] is not None:
            period = min_period(sym, fclk, info["pin"])
            need = info["tick_delay"] + 3
            if period is None or need >= period:
                have = "no datasheet rule bounds its period" if period is None else \
                    f"its shortest period is {float(period):.2f} ticks"
                raise TseError("R319", f"rule '{a.name.text}': tick_delay {info['tick_delay']} + 3 "
                               f"must stay below the period of {info['pin']}, but {have}",
                               a.name.span,
                               fields=[("fix", f"tick_delay <= {math.ceil(period) - 4}" if period
                                        else f"add rules {info['pin']} rise -> fall and fall -> "
                                        "rise to the datasheet")])
        checks[f"{a.name.text}.pos_plus_offset"] = (info["pos"] or 0) + info["offset"]
    if len(clocks) > 1:
        raise TseError("R303", f"role '{role.name.text}': both rules must share one clock "
                       "(RULE_CLK is per lane)", rules[1][0].name.span)
    if clocks:
        out["RULE_CLK"] = clocks.pop()
    return out, checks
