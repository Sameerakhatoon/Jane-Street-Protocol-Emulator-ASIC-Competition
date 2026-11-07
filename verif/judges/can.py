# SPDX-License-Identifier: Apache-2.0
"""CAN judge: data and remote frames from the logical bus level.

Written from the Bosch CAN 2.0 specification (ISO 11898-1 frame layer).
Dominant is 0, recessive 1. A frame starts with a dominant SOF after the
bus was recessive for at least 7 bit times (end of frame plus
intermission, or an error delimiter). The judge samples each bit at the
sample point and resynchronizes on every edge, as a CAN controller does
with hard and soft synchronization in a clean trace.

From SOF to the end of the CRC sequence the transmitter inserts a
complementary stuff bit after five equal bits; the judge removes them and
reports a sixth equal bit as a stuff error. The 15-bit CRC uses the
generator x^15 + x^14 + x^10 + x^8 + x^7 + x^4 + x^3 + 1 over the
destuffed bits from SOF to the end of the data field. After the CRC come
the CRC delimiter (recessive), the ACK slot (dominant when any receiver
acknowledged), the ACK delimiter and 7 recessive EOF bits. Base (11-bit)
and extended (29-bit) identifiers are both decoded.

Frame keys follow candump: 123#DEADBEEF, 123#R for a remote frame (with
the DLC when not zero, 123#R4), 8 hex digits for an extended identifier.
"""
from .common import Result, rate_check
from .vcd import X

ROLES = ("rx",)
REQUIRED = ("rx",)
PULLUP = False
OPTS = ("bitrate", "sample_point", "rate_tol")


def add_args(p):
    p.add_argument("--bitrate", type=float, required=True)
    p.add_argument("--sample-point", type=float, default=0.75, help="fraction of a bit")
    p.add_argument("--rate-tol", type=float, default=1.0, help="allowed bit rate error, percent")


class _Abort(Exception):
    pass


def crc15(bits):
    crc = 0
    for b in bits:
        nxt = b ^ ((crc >> 14) & 1)
        crc = (crc << 1) & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


class _Bits:
    def __init__(self, res, sig, sof, T, sp):
        self.res = res
        self.sig = sig
        self.T = T
        self.sp = sp
        self.anchor = sof
        self.n = 0
        self.t = sof
        self.run_level = None
        self.run = 0

    def raw(self):
        sig = self.sig
        while True:
            ts = self.anchor + (self.n + self.sp) * self.T
            i = sig.first_after(self.anchor)
            if i < len(sig) and sig.times[i] <= ts:
                self.anchor = sig.times[i]
                self.n = 0
                continue
            break
        self.n += 1
        self.t = ts
        v = sig.at(ts)
        if v == X:
            self.res.violate(ts, "unknown level", "X", "0 or 1")
            raise _Abort()
        return v

    def bit(self):
        if self.run == 5:
            v = self.raw()
            if v == self.run_level:
                self.res.violate(self.t, "stuff error", "6 equal bits", "5 equal bits")
                raise _Abort()
            self.run_level, self.run = v, 1
        v = self.raw()
        if v == self.run_level:
            self.run += 1
        else:
            self.run_level, self.run = v, 1
        return v

    def end_stuffing(self):
        if self.run == 5:
            v = self.raw()
            if v == self.run_level:
                self.res.violate(self.t, "stuff error", "6 equal bits", "5 equal bits",
                                 text="after the CRC sequence")
                raise _Abort()


def _int(bits):
    v = 0
    for b in bits:
        v = (v << 1) | b
    return v


def _frame(res, sig, sof, T, sp):
    """Decode one frame at sof; returns (time decoding stopped, decoded whole)."""
    b = _Bits(res, sig, sof, T, sp)
    seen = []

    def take(n):
        v = [b.bit() for _ in range(n)]
        seen.extend(v)
        return v

    try:
        if take(1)[0] != 0:
            res.violate(sof, "SOF", 1, 0, text="glitch, no frame")
            return b.t, False
        ident = _int(take(11))
        r, ide = take(2)
        ext = bool(ide)
        if ext:
            ident = (ident << 18) | _int(take(18))
            rtr = take(1)[0]
            take(2)
        else:
            rtr = r
            take(1)
        dlc = _int(take(4))
        data = [_int(take(8)) for _ in range(0 if rtr else min(dlc, 8))]
        want = crc15(seen)
        got = _int([b.bit() for _ in range(15)])
        b.end_stuffing()
        fid = ("%08X" if ext else "%03X") % ident
        if got != want:
            res.violate(b.t, "CRC", "0x%04X" % got, "0x%04X" % want, text="frame " + fid)
        if b.raw() != 1:
            res.violate(b.t, "CRC delimiter", 0, 1)
        if b.raw() != 0:
            res.violate(b.t, "ACK", "recessive", "dominant", text="no receiver acknowledged " + fid)
        if b.raw() != 1:
            res.violate(b.t, "ACK delimiter", 0, 1)
        for k in range(7):
            if b.raw() != 1:
                res.violate(b.t, "EOF", "dominant in bit %d" % k, "recessive")
                break
    except _Abort:
        res.frame(sof, b.t, "ERROR")
        return b.t, False
    end = b.t + (1 - sp) * T
    key = fid + "#" + ("R" + (str(dlc) if dlc else "") if rtr else "".join("%02X" % d for d in data))
    res.frame(sof, end, key, "dlc=%d" % dlc if dlc > 8 else "")
    return end, True


def decode(trace, bitrate, sample_point=0.75, rate_tol=1.0):
    res = Result(trace)
    sig = trace["rx"]
    T = trace.units(1.0 / bitrate)
    first = next((k for k, v in enumerate(sig.levels) if v != X), None)
    i = 1 if first is None else first + 1
    while i < len(sig):
        if not (sig.levels[i] == 0 and sig.levels[i - 1] == 1):
            i += 1
            continue
        sof = sig.times[i]
        if i - 1 != first and sof - sig.times[i - 1] < 7 * T * 0.999:
            i += 1
            continue
        end, ok = _frame(res, sig, sof, T, sample_point)
        if ok:
            rate_check(res, sof, sig.edges(sof, end), T, rate_tol, "bit rate", "bit/s", bitrate)
        i = max(i + 1, sig.first_after(end))
    return res.sort()
