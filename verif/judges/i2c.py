# SPDX-License-Identifier: Apache-2.0
"""I2C judge: transactions and bus timing from SCL and SDA.

Written from NXP UM10204 (I2C-bus specification). Both lines are open
drain, so Z reads as 1. SDA falling while SCL is high is a START (a
repeated START inside a transaction), SDA rising while SCL is high is a
STOP; otherwise SDA may change only while SCL is low and is sampled when
SCL rises. Bytes are eight bits most significant first plus the ACK bit
(0 = ACK). Clock stretching needs no special case: a target holding SCL low
only makes tLOW longer.

When SCL and SDA change at the same instant the judge treats the SDA
change as happening while SCL is low (after a falling SCL, before a rising
one), so the timing checks below report it instead of a false START/STOP.

Timing (characteristics of the SDA and SCL bus lines, Standard-mode,
Fast-mode and Fast-mode Plus), each a minimum: tHD;STA (START to first SCL
fall), tLOW, tHIGH, tSU;STA (SCL rise to repeated START), tSU;DAT (SDA
change to SCL rise), tHD;DAT (SCL fall to SDA change), tSU;STO (SCL rise
to STOP), tBUF (STOP to next START), plus the SCL clock period from the
maximum fSCL. Rise and fall times do not exist in a digital trace.

Frame key example: S 50W A 10 A Sr 50R A 7F N P
"""
from .common import Result, fmt_time
from .vcd import X

ROLES = ("scl", "sda")
REQUIRED = ("scl", "sda")
PULLUP = True
OPTS = ("speed", "timing", "require_ack", "thd_dat")

LIMITS = {
    "sm": dict(fscl=100e3, thd_sta=4.0e-6, tlow=4.7e-6, thigh=4.0e-6, tsu_sta=4.7e-6,
               thd_dat=0.0, tsu_dat=250e-9, tsu_sto=4.0e-6, tbuf=4.7e-6),
    "fm": dict(fscl=400e3, thd_sta=0.6e-6, tlow=1.3e-6, thigh=0.6e-6, tsu_sta=0.6e-6,
               thd_dat=0.0, tsu_dat=100e-9, tsu_sto=0.6e-6, tbuf=1.3e-6),
    "fmp": dict(fscl=1e6, thd_sta=0.26e-6, tlow=0.5e-6, thigh=0.26e-6, tsu_sta=0.26e-6,
                thd_dat=0.0, tsu_dat=50e-9, tsu_sto=0.26e-6, tbuf=0.5e-6),
}
NAMES = {"thd_sta": "tHD;STA", "tlow": "tLOW", "thigh": "tHIGH", "tsu_sta": "tSU;STA",
         "thd_dat": "tHD;DAT", "tsu_dat": "tSU;DAT", "tsu_sto": "tSU;STO", "tbuf": "tBUF",
         "period": "SCL period (fSCL)"}


def add_args(p):
    p.add_argument("--speed", default="sm", choices=sorted(LIMITS), help="sm 100 kHz, fm 400 kHz, fmp 1 MHz")
    p.add_argument("--no-timing", dest="timing", action="store_false")
    p.add_argument("--require-ack", action="store_true",
                   help="a NACK on an address or a written byte is a violation")
    p.add_argument("--thd-dat", type=float, default=None,
                   help="override the tHD;DAT minimum, seconds (0 in the specification)")


def _events(scl, sda):
    ev = []
    for t, v in zip(scl.times, scl.levels):
        ev.append((t, 2 if v == 1 else 0, 0, v))
    for t, v in zip(sda.times, sda.levels):
        ev.append((t, 1, 1, v))
    ev.sort()
    return ev


class _Bus:
    def __init__(self, res, lim, timing, require_ack):
        self.res = res
        self.lim = lim
        self.timing = timing
        self.require_ack = require_ack
        self.busy = False
        self.rise = None
        self.fall = None
        self.last_sda = None
        self.start = None
        self.stop = None
        self.hold_wait = False
        self.period_ok = False
        self.bits = []
        self.tokens = []
        self.t_frame = 0
        self.nbyte = 0
        self.rw = 0
        self.lows = []

    def chk(self, t, key, units):
        if self.timing:
            self.res.check_min(t, NAMES[key], units, self.lim[key])

    def scl(self, t, v, sda):
        if v == 1:
            if self.busy and self.fall is not None:
                self.chk(t, "tlow", t - self.fall)
                self.lows.append(t - self.fall)
            if self.busy and self.period_ok:
                self.chk(t, "period", t - self.rise)
            if self.busy and self.last_sda is not None and self.fall is not None and self.last_sda >= self.fall:
                self.chk(t, "tsu_dat", t - self.last_sda)
            self.rise = t
            self.period_ok = self.busy
            if self.busy:
                self.bits.append(sda)
                if len(self.bits) == 9:
                    self.byte(t)
        else:
            if self.busy and self.rise is not None:
                self.chk(t, "thigh", t - self.rise)
            if self.start is not None:
                self.chk(t, "thd_sta", t - self.start)
                self.start = None
            self.fall = t
            self.hold_wait = self.busy

    def sda(self, t, v, scl):
        if scl != 1:
            if self.hold_wait:
                self.chk(t, "thd_dat", t - self.fall)
                self.hold_wait = False
            self.last_sda = t
            return
        if v == 0:
            if self.busy:
                if self.rise is not None:
                    self.chk(t, "tsu_sta", t - self.rise)
                self.interrupted(t, "repeated START")
                self.tokens.append("Sr")
            else:
                if self.stop is not None:
                    self.chk(t, "tbuf", t - self.stop)
                self.tokens = ["S"]
                self.t_frame = t
                self.lows = []
            self.busy = True
            self.bits = []
            self.nbyte = 0
            self.start = t
            self.period_ok = False
        else:
            if self.busy:
                if self.rise is not None:
                    self.chk(t, "tsu_sto", t - self.rise)
                self.interrupted(t, "STOP")
                self.tokens.append("P")
                self.emit(t)
                self.busy = False
            self.stop = t
            self.start = None

    def interrupted(self, t, what):
        # the SCL high phase that carries a START or STOP is not a data bit
        if self.bits:
            self.bits.pop()
        if self.bits:
            self.res.violate(t, "byte interrupted", "%d bits" % len(self.bits), "9 bits",
                             text="%s inside a byte" % what)
            self.bits = []

    def byte(self, t):
        b = 0
        for v in self.bits[:8]:
            b = (b << 1) | (1 if v == 1 else 0)
        nack = self.bits[8] == 1
        if self.nbyte == 0:
            self.rw = b & 1
            if b >> 3 == 0x1E:
                self.tokens.append("10b:%X%s" % ((b >> 1) & 3, "R" if b & 1 else "W"))
            else:
                self.tokens.append("%02X%s" % (b >> 1, "R" if b & 1 else "W"))
        else:
            self.tokens.append("%02X" % b)
        self.tokens.append("N" if nack else "A")
        if nack and self.require_ack and (self.nbyte == 0 or self.rw == 0):
            self.res.violate(t, "NACK", 1, 0, text="byte %d not acknowledged" % self.nbyte)
        self.nbyte += 1
        self.bits = []

    def emit(self, t):
        info = ""
        if self.lows:
            info = "tLOW %s to %s" % (_fmt(self.res, min(self.lows)), _fmt(self.res, max(self.lows)))
        self.res.frame(self.t_frame, t, " ".join(self.tokens), info)


def _fmt(res, units):
    return fmt_time(res.trace.seconds(units))


def decode(trace, speed="sm", timing=True, require_ack=False, thd_dat=None):
    res = Result(trace)
    lim = dict(LIMITS[speed])
    lim["period"] = 1.0 / lim["fscl"]
    if thd_dat is not None:
        lim["thd_dat"] = thd_dat
    bus = _Bus(res, lim, timing, require_ack)
    c_scl, c_sda = 1, 1
    for t, _, which, v in _events(trace["scl"], trace["sda"]):
        if v == X:
            res.violate(t, "unknown level", "X", "0 or 1", text="scl" if which == 0 else "sda")
            continue
        if which == 0:
            if v != c_scl:
                c_scl = v
                bus.scl(t, v, c_sda)
        elif v != c_sda:
            c_sda = v
            bus.sda(t, v, c_scl)
    if bus.busy:
        res.violate(trace.end, "no STOP", "end of trace", "STOP")
        bus.emit(trace.end)
    return res.sort()
