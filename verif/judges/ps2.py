# SPDX-License-Identifier: Apache-2.0
"""PS/2 judge: device-to-host and host-to-device frames from CLK and DATA.

Written from the IBM PS/2 keyboard and auxiliary device interface
description. Both lines are open collector, so Z reads as 1. The device
always generates the clock, 10 to 16.7 kHz, so each clock half period is
30 to 50 us.

Device to host: 11 bits, each read by the host on the falling edge of
CLK: start (0), 8 data bits least significant first, odd parity, stop (1).
The device changes DATA only while CLK is high, at least 5 us after the
rising edge and at least 5 us before the next falling edge.

Host to device: the host holds CLK low for at least 100 us, pulls DATA low
(the start bit) and releases CLK. The device then clocks the frame and
reads each bit on the rising edge: 8 data bits, odd parity, stop (the host
releases DATA); then it acknowledges by pulling DATA low before its 11th
falling edge.

Frame keys: "dev 1C", "host ED".
"""
from .common import Result

ROLES = ("clk", "data")
REQUIRED = ("clk", "data")
PULLUP = True
OPTS = ("timing",)

HALF_MIN = 30e-6
HALF_MAX = 50e-6
RTS_MIN = 100e-6
DATA_GAP = 5e-6
INHIBIT = 60e-6     # a CLK low longer than any device half period is the host


def add_args(p):
    p.add_argument("--no-timing", dest="timing", action="store_false")


def _bits_value(bits):
    v = 0
    for k, b in enumerate(bits):
        v |= b << k
    return v


def decode(trace, timing=True):
    res = Result(trace)
    clk, dat = trace["clk"], trace["data"]
    falls = clk.edges(-1, trace.end, 0)
    rises = clk.edges(-1, trace.end, 1)
    inhibit = trace.units(INHIBIT)
    gap = trace.units(2 * HALF_MAX * 2)
    ri = 0
    fi = 0
    while fi < len(falls):
        f = falls[fi]
        while ri < len(rises) and rises[ri] <= f:
            ri += 1
        r = rises[ri] if ri < len(rises) else trace.end
        if r - f > inhibit:
            if dat.before(r) == 0 and ri < len(rises):
                if timing:
                    res.check_min(f, "host request-to-send CLK low", r - f, RTS_MIN)
                fi = _host_frame(res, clk, dat, falls, rises, fi + 1, r, gap, timing)
            else:
                fi += 1
            continue
        if dat.before(f) == 0:
            fi = _dev_frame(res, clk, dat, falls, rises, fi, gap, timing)
        else:
            fi += 1
    return res.sort()


def _clock_halves(res, clk, t0, t1, timing):
    if not timing:
        return
    ch = [t0] + clk.edges(t0, t1)
    for a, b in zip(ch, ch[1:]):
        rule = "CLK low" if clk.at(a) == 0 else "CLK high"
        res.check_range(a, rule, b - a, HALF_MIN, HALF_MAX)


def _dev_frame(res, clk, dat, falls, rises, fi, gap, timing):
    fs = falls[fi:fi + 11]
    if len(fs) < 11 or any(b - a > gap for a, b in zip(fs, fs[1:])):
        res.violate(fs[0], "incomplete frame", "%d clocks" % _run(fs, gap), "11 clocks", text="device to host")
        return fi + max(1, _run(fs, gap))
    bits = [1 if dat.before(f) != 0 else 0 for f in fs]
    value = _bits_value(bits[1:9])
    if sum(bits[1:10]) % 2 != 1:
        res.violate(fs[9], "parity", bits[9], 1 - sum(bits[1:9]) % 2, text="dev %02X" % value)
    if bits[10] != 1:
        res.violate(fs[10], "stop bit", 0, 1, text="dev %02X" % value)
    end = fs[10]
    nxt = clk.first_after(end)
    t_end = clk.times[nxt] if nxt < len(clk) else end
    _clock_halves(res, clk, fs[0], t_end, timing)
    if timing:
        for t in dat.edges(fs[0], end):
            if clk.at(t) == 0:
                res.violate(t, "DATA change while CLK low", "CLK low", "CLK high", text="device to host")
                continue
            r = clk.last_change_before(t + 1)
            res.check_min(t, "DATA after CLK rise", t - r, DATA_GAP)
            i = clk.first_after(t)
            if i < len(clk):
                res.check_min(t, "DATA before CLK fall", clk.times[i] - t, DATA_GAP)
    res.frame(fs[0], t_end, "dev %02X" % value)
    return fi + 11


def _host_frame(res, clk, dat, falls, rises, fi, release, gap, timing):
    fs = falls[fi:fi + 11]
    if len(fs) < 11 or any(b - a > gap for a, b in zip([release] + fs, fs)):
        res.violate(release, "incomplete frame", "%d clocks" % len(fs), "11 clocks", text="host to device")
        return fi + max(1, len(fs))
    rs = [rises[k] for k in range(len(rises)) if fs[0] < rises[k] < fs[10]][:10]
    if len(rs) < 10:
        res.violate(release, "incomplete frame", "%d rising edges" % len(rs), "10", text="host to device")
        return fi + 11
    bits = [1 if dat.before(r) != 0 else 0 for r in rs]
    value = _bits_value(bits[0:8])
    if sum(bits[0:9]) % 2 != 1:
        res.violate(rs[8], "parity", bits[8], 1 - sum(bits[0:8]) % 2, text="host %02X" % value)
    if bits[9] != 1:
        res.violate(rs[9], "stop bit", 0, 1, text="host %02X" % value)
    if dat.before(fs[10]) != 0:
        res.violate(fs[10], "no ACK", 1, 0, text="device did not acknowledge host %02X" % value)
    i = clk.first_after(fs[10])
    t_end = clk.times[i] if i < len(clk) else fs[10]
    _clock_halves(res, clk, fs[0], t_end, timing)
    res.frame(release, t_end, "host %02X" % value)
    return fi + 11


def _run(fs, gap):
    n = 1
    while n < len(fs) and fs[n] - fs[n - 1] <= gap:
        n += 1
    return n
