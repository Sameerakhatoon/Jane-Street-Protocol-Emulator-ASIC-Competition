# SPDX-License-Identifier: Apache-2.0
"""ISA version 1 of the TSE lane: field layout, decode, encode, illegal words.

Hand transcription of the ISA, kept independent of the generated decoder in
tools/tseasm on purpose: test_08 checks both readings agree on all 65,536
words, and test_01 pins the count of legal words (55,320).
"""

# name: (op, fixed mask in [10:0], fixed value, fields as (name, lsb, width))
FORMATS = {
    "JMP":  (0, 0, 0, (("cond", 8, 3), ("target", 0, 8))),
    "WAIT": (1, 0, 0, (("pol", 10, 1), ("src", 8, 2), ("idx", 5, 3), ("to", 4, 1),
                       ("alt", 3, 1), ("delay", 0, 3))),
    "SET":  (2, 0, 0, (("dst", 8, 3), ("imm", 0, 8))),
    "MOV":  (3, 0, 0, (("dst", 8, 3), ("src", 5, 3), ("delay", 0, 5))),
    "OUT":  (4, 0, 0, (("dst", 8, 3), ("n1", 3, 5), ("delay", 0, 3))),
    "IN":   (5, 0, 0, (("src", 8, 3), ("n1", 3, 5), ("delay", 0, 3))),
    "PUSH": (6, 0x600, 0x000, (("blk", 8, 1),)),
    "PULL": (6, 0x600, 0x200, (("blk", 8, 1),)),
    "CSRW": (6, 0x700, 0x400, (("addr", 0, 8),)),
    "STW":  (6, 0x700, 0x500, (("ind", 7, 1), ("addr", 0, 7))),
    "CSRR": (6, 0x700, 0x600, (("addr", 0, 8),)),
    "LDW":  (6, 0x700, 0x700, (("ind", 7, 1), ("addr", 0, 7))),
    "DLY":  (7, 0x600, 0x000, (("d", 0, 9),)),
    "TMR":  (7, 0x600, 0x200, (("cclr", 8, 1), ("gwe", 7, 1), ("gsel", 5, 2), ("delay", 0, 5))),
    "SIG":  (7, 0x600, 0x400, (("mask", 4, 4), ("val", 0, 4))),
    "EMIT": (7, 0x600, 0x600, (("code", 3, 6), ("delay", 0, 3))),
}

JMP_COND = ("ALWAYS", "XZ", "XDEC", "YDEC", "XNEY", "PIN", "TOUT", "GUARD")
WAIT_SRC = ("PIN", "EV", "FLAG", "CHIP")
SET_DST = ("PINS", "PINDIRS", "X", "Y", "T", "XH", "IRQ", "CMD")
MOV_DST = ("X", "Y", "PINS", "PINDIRS", "ISR", "OSR", "T", "PC")
MOV_SRC = ("X", "Y", "PINS", "ISR", "OSR", "T", "NOTX", "C")
OUT_DST = ("PINS", "PINDIRS", "X", "Y", "NULL", "T")
IN_SRC = ("PINS", "X", "Y", "ZEROS", "ONES", "OSR")

# class L configuration words: CSRW costs 3, CSRR reads 0
CLASS_L = frozenset([0x00, 0x01, 0x02, 0x03, 0x04, 0x08, 0x09, 0x10, 0x11, 0x12, 0x13,
                     0x14, 0x15, 0x18, 0x19, 0x1A, 0x1B,
                     0x40, 0x41, 0x42, 0x44, 0x45, 0x46, 0x48])


class Illegal(Exception):
    pass


def _fields_mask(fixed, fields):
    m = fixed
    for _, lsb, width in fields:
        m |= ((1 << width) - 1) << lsb
    return m


def decode(w):
    """Return (name, ss, fields) for a legal word, raise Illegal otherwise."""
    op, ss, arg = w >> 13, (w >> 11) & 3, w & 0x7FF
    for name, (fop, fmask, fval, fields) in FORMATS.items():
        if fop != op or (arg & fmask) != fval:
            continue
        if arg & ~_fields_mask(fmask, fields) & 0x7FF:
            raise Illegal(f"{w:#06x}: reserved bits set")
        f = {n: (arg >> lsb) & ((1 << width) - 1) for n, lsb, width in fields}
        if name == "OUT" and f["dst"] >= 6 or name == "IN" and f["src"] >= 6:
            raise Illegal(f"{w:#06x}: reserved selector")
        if name in ("LDW", "STW") and f["ind"] and f["addr"]:
            raise Illegal(f"{w:#06x}: indirect with nonzero address")
        return name, ss, f
    raise Illegal(f"{w:#06x}: no format")


def encode(name, ss=0, **f):
    op, _, fval, fields = FORMATS[name]
    w = (op << 13) | (ss << 11) | fval
    for n, lsb, width in fields:
        v = f.pop(n, 0)
        assert 0 <= v < (1 << width), (name, n, v)
        w |= v << lsb
    assert not f, f"unknown fields {f}"
    return w


def disasm(w):
    try:
        name, ss, f = decode(w)
    except Illegal:
        return f".word {w:#06x}"
    args = ", ".join(f"{k}={v}" for k, v in f.items())
    return f"{name} {args}" + (f" side {ss}" if ss else "")


def pack(prog):
    """Instructions to 32-bit SRAM words, even address in bits 15:0."""
    p = list(prog) + [0] * (len(prog) & 1)
    return [p[i] | (p[i + 1] << 16) for i in range(0, len(p), 2)]
