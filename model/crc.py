"""Lane CRC unit and a textbook CRC for the catalogue.

The register is left-aligned: the feedback tap is bit 31 for every width, so
CRC_POLY, CRC_INIT, CRC_XOR and CRC_CHECK hold their values shifted left by
32 - width. Bits enter one per cycle (bit input) or a byte at a time, one bit
per cycle for 8 cycles (byte input, refin picks the bit order). result is
the register XOR CRC_XOR, left-aligned, or reflected when refout is set.
For TX, tx_load folds CRC_XOR into the register and tx_shift sends it MSB
first on tx_bit. The ISA text does not order clear, tx_load, tx_shift and a
bit arriving in the same cycle; the model takes clear first, then tx_load,
then tx_shift, then the data bit, as the RTL does.
"""
from dataclasses import dataclass

M32 = 0xFFFFFFFF


def rev(v, w):
    return int(f"{v:0{w}b}"[::-1], 2)


def crc_textbook(data, width, poly, init, refin, refout, xorout):
    """Bitwise MSB-first CRC over bytes, the catalogue definition."""
    top, mask, r = 1 << (width - 1), (1 << width) - 1, init
    for byte in data:
        if refin:
            byte = rev(byte, 8)
        for k in range(7, -1, -1):
            fb = bool(r & top) ^ ((byte >> k) & 1)
            r = ((r << 1) & mask) ^ (poly if fb else 0)
    if refout:
        r = rev(r, width)
    return r ^ xorout


def left(v, width):
    return (v << (32 - width)) & M32


@dataclass
class CrcIn:
    clear: int = 0
    bit_valid: int = 0
    bit_in: int = 0
    byte_valid: int = 0
    byte_in: int = 0
    tx_load: int = 0
    tx_shift: int = 0


class Crc:
    def __init__(self, poly=0, init=0, xo=0, check=0, wm1=31, refin=0, refout=0, right_align=False):
        self.poly, self.init, self.xo, self.check = poly, init, xo, check
        self.wm1, self.refin, self.refout, self.right_align = wm1, refin, refout, right_align
        self.crc = self.bsr = self.bcnt = 0

    @classmethod
    def catalogue(cls, width, poly, init, refin, refout, xorout, check=0):
        xo = rev(xorout, width) if refout else xorout
        return cls(left(poly, width), left(init, width), left(xo, width), left(check, width),
                   width - 1, refin, refout)

    def out(self):
        t = self.crc ^ self.xo
        if self.refout:
            res = rev(t, 32)
        elif self.right_align:
            res = t >> (31 - self.wm1)
        else:
            res = t
        return dict(busy=int(self.bcnt != 0), tx_bit=self.crc >> 31, result=res,
                    check_ok=int(t == self.check))

    def step(self, i):
        o = self.out()
        busy = self.bcnt != 0
        din = ((self.bsr & 1) if self.refin else (self.bsr >> 7)) if busy else i.bit_in
        sh = (self.crc << 1) & M32
        if i.clear:
            self.crc = self.init
        elif i.tx_load:
            self.crc = self.crc ^ self.xo
        elif i.tx_shift:
            self.crc = sh
        elif i.bit_valid or busy:
            self.crc = sh ^ (self.poly if (self.crc >> 31) ^ din else 0)
        if i.byte_valid and not busy:
            self.bsr, self.bcnt = i.byte_in & 0xFF, 8
        elif busy:
            self.bsr = (self.bsr >> 1) if self.refin else ((self.bsr << 1) & 0xFF)
            self.bcnt -= 1
        return o
