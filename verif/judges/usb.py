"""USB low-speed judge: packets from D+ and D-.

Written from the USB 2.0 specification, chapter 7 (electrical: line
states, NRZI, bit stuffing, SYNC, EOP, timing) and chapter 8 (packets:
PID, fields, CRC). Low speed runs at 1.5 Mb/s +/- 1.5 percent. Line
states: J is D- high and D+ low (the low-speed idle), K the opposite, SE0
both low; both high (SE1) is illegal.

A packet starts when the idle J changes to K. Bits are NRZI coded: a 0 is
a change of state, a 1 no change. The first 8 bits are the SYNC pattern
00000001 (KJKJKJKK on the wire). From SYNC to the end of the packet the
transmitter inserts a 0 after six 1s in a row, even when the six 1s are the
last bits before EOP; a seventh 1 is a stuff error. The packet ends with
an EOP: SE0 for two bit times (source width 1.25 to 1.50 us) then J. The
judge samples each bit in its middle and resynchronizes on every change,
like a receiver's DPLL. An SE0 or SE1 shorter than 210 ns between J and K
is the skew of a differential transition and is not a line state.

Fields go least significant bit first. The PID byte is a 4-bit type and
its complement. Tokens carry a 7-bit address, a 4-bit endpoint and CRC5
(x^5 + x^2 + 1), SOF an 11-bit frame number and CRC5, data packets the
payload (at most 8 bytes at low speed) and CRC16 (x^16 + x^15 + x^2 + 1).
Both CRCs start from all ones and are sent inverted; the judge recomputes
them over the received field bits. SE0 for two bit times from idle is a
low-speed keep-alive; SE0 for 2.5 us or more is a bus reset.

Frame keys: "SETUP 0.0", "IN 3.1", "SOF 710", "DATA0 8006000100004000",
"ACK", "NAK", "STALL", "KEEPALIVE", "RESET", "ERROR".
"""
import bisect

from .common import Result, fmt_time, rate_check

ROLES = ("dp", "dm")
REQUIRED = ("dp", "dm")
PULLUP = False
OPTS = ("rate_tol",)

BITRATE = 1.5e6
EOP_MIN = 1.25e-6
EOP_MAX = 1.50e-6
SE0_SKEW = 210e-9
RESET_MIN = 2.5e-6
MAX_PAYLOAD = 8

J, K, SE0, SE1, UNK = "J", "K", "SE0", "SE1", "?"
PIDS = {0x1: "OUT", 0x9: "IN", 0x5: "SOF", 0xD: "SETUP", 0x3: "DATA0", 0xB: "DATA1",
        0x7: "DATA2", 0xF: "MDATA", 0x2: "ACK", 0xA: "NAK", 0xE: "STALL", 0x6: "NYET",
        0xC: "PRE", 0x8: "SPLIT", 0x4: "PING"}
TOKENS = ("OUT", "IN", "SETUP", "PING")
DATA = ("DATA0", "DATA1", "DATA2", "MDATA")


def add_args(p):
    p.add_argument("--rate-tol", type=float, default=1.5, help="allowed bit rate error, percent")


def crc5(bits):
    crc = 0x1F
    for b in bits:
        crc = (crc >> 1) ^ 0x14 if (crc ^ b) & 1 else crc >> 1
    return crc ^ 0x1F


def crc16(bits):
    crc = 0xFFFF
    for b in bits:
        crc = (crc >> 1) ^ 0xA001 if (crc ^ b) & 1 else crc >> 1
    return crc ^ 0xFFFF


def _lsb(bits):
    v = 0
    for k, b in enumerate(bits):
        v |= b << k
    return v


def line_states(dp, dm, skew):
    """Merged (times, states) of the two lines, without transition skew SE0."""
    ts = sorted(set(dp.times) | set(dm.times))
    times, states = [], []
    for t in ts:
        p, m = dp.at(t), dm.at(t)
        if p > 1 or m > 1:
            s = UNK
        else:
            s = (SE0, J, K, SE1)[p << 1 | m]
        if states and states[-1] == s:
            continue
        times.append(t)
        states.append(s)
    out_t, out_s = [], []
    for i, (t, s) in enumerate(zip(times, states)):
        if (s in (SE0, SE1) and 0 < i < len(times) - 1 and times[i + 1] - t < skew
                and {states[i - 1], states[i + 1]} == {J, K}):
            continue
        if out_s and out_s[-1] == s:
            continue
        out_t.append(t)
        out_s.append(s)
    return out_t, out_s


class _Line:
    def __init__(self, times, states):
        self.times = times
        self.states = states

    def idx(self, t):
        return bisect.bisect_right(self.times, t) - 1

    def at(self, t):
        i = self.idx(t)
        return self.states[i] if i >= 0 else UNK


def decode(trace, rate_tol=1.5):
    res = Result(trace)
    times, states = line_states(trace["dp"], trace["dm"], trace.units(SE0_SKEW))
    line = _Line(times, states)
    T = trace.units(1.0 / BITRATE)
    i = 1
    while i < len(times):
        s, prev = states[i], states[i - 1]
        if s == SE1:
            res.violate(times[i], "SE1", "D+ and D- high", "J, K or SE0")
        if s == SE0 and prev == J:
            w = (times[i + 1] if i + 1 < len(times) else trace.end) - times[i]
            if trace.seconds(w) >= RESET_MIN:
                res.frame(times[i], times[i] + w, "RESET", fmt_time(trace.seconds(w)))
            else:
                res.frame(times[i], times[i] + w, "KEEPALIVE")
                _check_eop(res, trace, line, times[i], w)
            i += 1
            continue
        if s == K and prev == J:
            i = _packet(res, trace, line, i, T, rate_tol)
            continue
        i += 1
    return res.sort()


def _check_eop(res, trace, line, t, w):
    res.check_range(t, "EOP width", w, EOP_MIN, EOP_MAX, text="SE0")
    after = line.at(t + w)
    if after != J:
        res.violate(t + w, "EOP J", after, J, text="line after SE0")


def _packet(res, trace, line, i, T, rate_tol):
    """Decode the packet whose SYNC starts at change i; returns the next change index."""
    times, states = line.times, line.states
    sop = times[i]
    anchor, n = sop, 0
    last = J
    raw = []            # NRZI-decoded bits with stuff bits, up to EOP
    se0_at = None
    while True:
        ts = anchor + (n + 0.5) * T
        j = bisect.bisect_right(times, anchor)
        if j < len(times) and times[j] <= ts:
            anchor, n = times[j], 0
            continue
        n += 1
        s = line.at(ts)
        if s == SE0:
            se0_at = times[line.idx(ts)]
            break
        if s not in (J, K) or ts > trace.end:
            s = "end of trace" if ts > trace.end else s
            res.violate(ts, "line state", s, "J or K", text="inside a packet")
            res.frame(sop, ts, "ERROR")
            return line.idx(ts) + 1
        raw.append(1 if s == last else 0)
        last = s
        if len(raw) > 8 * 1100:
            res.violate(sop, "no EOP", "%d bits" % len(raw), "EOP")
            res.frame(sop, ts, "ERROR")
            return line.idx(ts) + 1

    k = line.idx(se0_at)
    w = (times[k + 1] if k + 1 < len(times) else trace.end) - se0_at
    _check_eop(res, trace, line, se0_at, w)
    nxt = k + 1
    rate_check(res, sop, times[i + 1:k + 1], T, rate_tol, "bit rate", "bit/s", BITRATE)

    if raw[:8] != [0, 0, 0, 0, 0, 0, 0, 1]:
        res.violate(sop, "SYNC", "".join(map(str, raw[:8])), "00000001")
        res.frame(sop, se0_at, "ERROR")
        return nxt
    bits, run = [], 0
    for k2, b in enumerate(raw):
        if run == 6:
            if b == 1:
                res.violate(sop, "stuff error", "7 ones", "a 0 after 6 ones", text="bit %d" % k2)
                res.frame(sop, se0_at, "ERROR")
                return nxt
            run = 0
            continue
        run = run + 1 if b else 0
        bits.append(b)
    if run == 6:
        res.violate(se0_at, "stuff error", "EOP after 6 ones", "a stuff bit before EOP")
    body = bits[8:]
    if len(body) < 8 or len(body) % 8:
        res.violate(se0_at, "packet length", "%d bits" % len(body), "whole bytes, at least 8")
        res.frame(sop, se0_at, "ERROR")
        return nxt
    pid = _lsb(body[:4])
    chk = _lsb(body[4:8])
    if chk != pid ^ 0xF:
        res.violate(sop, "PID check", "%X%X" % (chk, pid), "%X%X" % (pid ^ 0xF, pid))
        res.frame(sop, se0_at, "ERROR")
        return nxt
    name = PIDS[pid]
    f = body[8:]
    if name in TOKENS or name == "SOF":
        if len(f) != 16:
            res.violate(sop, "packet length", "%d bits" % len(f), "16 bits after the PID", text=name)
            res.frame(sop, se0_at, name + " ERROR")
            return nxt
        want = crc5(f[:11])
        got = _lsb(f[11:])
        if got != want:
            res.violate(sop, "CRC5", "0x%02X" % got, "0x%02X" % want, text=name)
        key = "SOF %X" % _lsb(f[:11]) if name == "SOF" else "%s %d.%d" % (name, _lsb(f[:7]), _lsb(f[7:11]))
    elif name in DATA:
        if len(f) < 16:
            res.violate(sop, "packet length", "%d bits" % len(f), ">= 16 bits after the PID", text=name)
            res.frame(sop, se0_at, name + " ERROR")
            return nxt
        payload = f[:-16]
        want = crc16(payload)
        got = _lsb(f[-16:])
        if got != want:
            res.violate(sop, "CRC16", "0x%04X" % got, "0x%04X" % want, text=name)
        nbytes = len(payload) // 8
        if nbytes > MAX_PAYLOAD:
            res.violate(sop, "low-speed payload", "%d bytes" % nbytes, "<= %d bytes" % MAX_PAYLOAD)
        key = name + " " + "".join("%02X" % _lsb(payload[8 * j:8 * j + 8]) for j in range(nbytes))
        key = key.rstrip()
    else:
        if f:
            res.violate(sop, "packet length", "%d bits" % len(f), "0 bits after the PID", text=name)
        key = name
    res.frame(sop, se0_at + w, key)
    return nxt
