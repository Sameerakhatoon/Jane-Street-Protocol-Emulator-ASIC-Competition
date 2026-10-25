"""Frame layouts: a `frame` declaration as fields with bit counts, constants and CRC ranges.

Rules the lowering relies on:
  - bit order: a frame goes least significant bit first when the CRC that covers
    it reflects its input (refin), most significant bit first otherwise (CRC-16/USB,
    CRC-32 and CRC-5/USB reflect, CRC-15/CAN does not); a frame without a CRC
    has no stated order and is rejected where the order matters;
  - a constant field `x : bits[n] = v` is v in n bits; `bytes[n] = v` is n bytes of v;
  - `bytes[a..b]` takes its length from the closest earlier `uint` field, capped at b
    (a CAN DLC of 9 to 15 means 8 bytes); with no such field the length is open:
    the sender's host knows it, a receiver learns it from the end of the frame;
  - a field `from ROLE` is sent by that role, so a role receiving the frame stops
    receiving before its own first field.
"""
from dataclasses import dataclass, field

from .diag import TseError


@dataclass
class Field:
    name: str
    node: object
    lo: int                     # least bits
    hi: int                     # most bits
    const: int = None
    nostuff: bool = False
    sender: str = None          # from ROLE
    crc: tuple = None           # (assist name, first field, last field)
    count: str = None           # uint field giving the byte count of this field
    unit: int = 1               # 8 for bytes

    @property
    def fixed(self):
        return self.lo == self.hi


@dataclass
class Layout:
    frame: object
    fields: list = field(default_factory=list)
    lsb_first: bool = None
    crc: object = None          # the Assist of the CRC field

    def index(self, name):
        return next(i for i, f in enumerate(self.fields) if f.name == name)

    def get(self, name):
        return self.fields[self.index(name)]

    def offset(self, i):
        """(least, most) bit offset of field i from the first bit."""
        return sum(f.lo for f in self.fields[:i]), sum(f.hi for f in self.fields[:i])

    def total(self):
        return sum(f.lo for f in self.fields), sum(f.hi for f in self.fields)

    def bits_of(self, f):
        """Transmission-order bits of a constant field."""
        if f.unit == 8:
            one = byte_bits(f.const, 8, self.lsb_first)
            return one * (f.lo // 8)
        return byte_bits(f.const, f.lo, self.lsb_first)

    def prefix(self):
        """(bits, number of fields) of the constant fields the frame starts with."""
        out, n = [], 0
        for f in self.fields:
            if f.const is None or not f.fixed:
                break
            out += self.bits_of(f)
            n += 1
        return out, n

    def crc_field(self):
        return next((i for i, f in enumerate(self.fields) if f.crc), None)

    def first_sent_by(self, role):
        return next((i for i, f in enumerate(self.fields) if f.sender == role), None)

    def variable(self):
        return [i for i, f in enumerate(self.fields) if not f.fixed]


def byte_bits(v, n, lsb_first):
    order = range(n) if lsb_first else range(n - 1, -1, -1)
    return [(v >> k) & 1 for k in order]


def layout(sym, name, span):
    fr = sym.frames.get(name)
    if fr is None:
        raise TseError("R300", f"not supported by this version of tsec: '{name}' is not a frame",
                       span)
    lay = Layout(fr)
    for ff in fr.fields:
        ty, tname = ff.type, ff.type.name.text
        unit = 8 if tname == "bytes" else 1
        if tname == "bit":
            lo = hi = 1
        elif tname in ("bits", "uint", "bytes"):
            lo = int(sym.eval(ty.width).v) * unit
            hi = int(sym.eval(ty.width_hi).v) * unit if ty.width_hi is not None else lo
        elif tname in sym.enums:
            lo = hi = int(sym.eval(sym.enums[tname].type.width).v)
        else:
            raise TseError("R300", f"not supported by this version of tsec: field '{ff.name.text}' "
                           f"of type {tname} in a frame", ff.span)
        f = Field(ff.name.text, ff, lo, hi, unit=unit)
        if ff.value is not None:
            f.const = int(sym.eval(ff.value).v)
        for at in ff.attrs:
            if at.kind == "nostuff":
                f.nostuff = True
            elif at.kind == "from":
                f.sender = at.args[0].text
            elif at.kind == "crc":
                f.crc = tuple(x.text for x in at.args)
            elif at.kind == "if":
                raise TseError("R300", "not supported by this version of tsec: a conditional "
                               f"frame field ('{ff.name.text}')", ff.span)
        if not f.fixed and unit == 8:
            counts = [g for g in lay.fields if g.node.type.name.text == "uint"]
            if counts:
                f.count = counts[-1].name
        lay.fields.append(f)
    k = lay.crc_field()
    if k is not None:
        lay.crc = sym.assists[lay.fields[k].crc[0]]
        refin = next((p for p in lay.crc.params if p.name.text == "refin"), None)
        lay.lsb_first = refin is not None and printer_word(refin.value) in ("true", "1")
    return lay


def printer_word(e):
    from . import ast as A
    if isinstance(e, A.Ref):
        return e.name.text
    if isinstance(e, A.Num):
        return str(int(e.value))
    return ""
