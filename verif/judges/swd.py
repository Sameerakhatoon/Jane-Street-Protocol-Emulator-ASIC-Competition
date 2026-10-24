"""SWD judge: Serial Wire Debug packets from SWCLK and SWDIO.

Written from the ARM Debug Interface Architecture Specification (ADIv5),
serial wire protocol. SWDIO has a pull-up, so Z reads as 1. The host
writes SWDIO on the falling edge of SWCLK and the target reads it on the
rising edge; the target writes on the rising edge and the host reads on
the next rising edge. So every bit, host or target, is the level just
before the rising edge that ends its cycle; a target bit was launched at
the rising edge before. A target with a long output delay (its bit
appearing after the falling edge) is still read correctly, as a host
would read it.

A packet, one bit per cycle: 8 request bits from the host (Start=1,
APnDP, RnW, A[2:3], Parity even over the four, Stop=0, Park=1), one
turnaround cycle, a 3-bit ACK from the target (OK 001, WAIT 010, FAULT
100, sent least significant first; 111 means nobody answered). The
target launches ACK[0] at the rising edge that ends the turnaround, so a
probe reads it at the next one; that is how OpenOCD and CMSIS-DAP count.
After OK: a read has 32 data bits and even parity from the target then a
turnaround; a write has a turnaround then 32 data bits and parity from
the host. Data is least significant bit first. At least 50 host cycles
with SWDIO high form a line reset. A 16-bit select sequence right after
one is reported too: JTAG-to-SWD 0xE79E, SWD-to-JTAG 0xE73C (OpenOCD
sends it when it shuts down) and SWD-to-dormant 0xE3BC; after the last
two the line no longer carries SWD, so nothing is decoded until the next
line reset. A dormant port wakes on the 128-bit selection alert
0x19BC0EA2E3DDAFE986852D956209F392 (least significant bit first), 4 low
cycles and the SWD activation code 0x1A, reported as DORMANT_TO_SWD;
OpenOCD sends it when its first JTAG-to-SWD attempt gets no answer. An
alert with another activation code is SELECTION_ALERT. OpenOCD puts a
JTAG-to-dormant sequence in front (at least 5 high cycles, then the 31
bits 0x33BBBBBA), reported as JTAG_TO_DORMANT.

Optional roles host_oe and target_oe say who drives SWDIO (1 = driving),
as a simulator or a probe with a direction pin can record. With them the
judge also reports both sides driving at once (longer than --overlap)
and a bit read while the side that owns it was not driving.

Frame keys: "DP R 0x0 OK 2BA01477", "AP W 0xC WAIT", "LINE_RESET",
"JTAG_TO_SWD", "SWD_TO_JTAG", "SWD_TO_DORMANT", "JTAG_TO_DORMANT",
"DORMANT_TO_SWD", "SELECTION_ALERT".
"""
from .common import Result, fmt_time

ROLES = ("swclk", "swdio", "host_oe", "target_oe")
REQUIRED = ("swclk", "swdio")
PULLUP = True
OPTS = ("overlap",)
ACKS = {1: "OK", 2: "WAIT", 4: "FAULT", 7: "NOACK"}
SELECT = {0xE79E: "JTAG_TO_SWD", 0xE73C: "SWD_TO_JTAG", 0xE3BC: "SWD_TO_DORMANT"}
ALERT = 0x19BC0EA2E3DDAFE986852D956209F392
ALERT_BITS = "".join(str((ALERT >> k) & 1) for k in range(128))
SWD_ACTIVATION = 0x1A
TO_DORMANT_BITS = "".join(str((0x33BBBBBA >> k) & 1) for k in range(31))


def add_args(p):
    p.add_argument("--overlap", type=float, default=0.0, metavar="SECONDS",
                   help="longest time both sides may drive SWDIO at once (default 0)")


def decode(trace, overlap=0.0):
    res = Result(trace)
    clk, dio = trace["swclk"], trace["swdio"]
    rises = clk.edges(-1, trace.end, 1)
    bit = [1 if dio.before(r) != 0 else 0 for r in rises]
    oe = {"host": trace["host_oe"] if "host_oe" in trace else None,
          "target": trace["target_oe"] if "target_oe" in trace else None}
    if oe["host"] is not None and oe["target"] is not None:
        _contention(res, oe["host"], oe["target"], trace, overlap)

    def owner(side, k0, k1, what):
        s = oe[side]
        if s is None:
            return
        for k in range(k0, k1):
            if s.before(rises[k]) != 1:
                res.violate(rises[k], "driver", "%s not driving" % side, "%s drives the %s" % (side, what))
                return

    n = len(rises)
    s = "".join(map(str, bit))
    alerts = _find_all(s, ALERT_BITS)
    to_dormant = _find_all(s, TO_DORMANT_BITS)
    i = 0
    while i < n:
        if i in alerts:
            i = _wake_up(res, rises, bit, i)
            continue
        if bit[i] != 1:
            i += 1
            continue
        run = 0
        while i + run < n and bit[i + run] == 1:
            run += 1
        if run < 50 and i + run in alerts:
            i += run                    # idle high before a selection alert
            continue
        if 5 <= run < 50 and i + run in to_dormant:
            res.frame(rises[i + run], rises[i + run + 30], "JTAG_TO_DORMANT")
            i += run + 31
            continue
        if run >= 50:
            res.frame(rises[i], rises[i + run - 1], "LINE_RESET", "%d cycles" % run)
            i += run
            if i in to_dormant:
                res.frame(rises[i], rises[i + 30], "JTAG_TO_DORMANT")
                i += 31
                continue
            seq = SELECT.get(_lsb(bit[i:i + 16])) if i + 16 <= n else None
            if seq:
                res.frame(rises[i], rises[i + 15], seq)
                i += 16
                if seq != "JTAG_TO_SWD":
                    # the port has left SWD: skip to the next line reset
                    while i < n and not (i + 50 <= n and all(bit[i:i + 50])):
                        i += 1
            continue
        if i + 8 > n:
            res.violate(rises[i], "truncated request", "%d bits" % (n - i), "8 bits")
            break
        rq = bit[i:i + 8]
        apndp, rnw, addr = rq[1], rq[2], (rq[3] | rq[4] << 1) << 2
        if (rq[1] + rq[2] + rq[3] + rq[4] + rq[5]) & 1:
            res.violate(rises[i], "request parity", rq[5], (rq[1] + rq[2] + rq[3] + rq[4]) & 1)
        if rq[6] != 0:
            res.violate(rises[i], "request stop bit", rq[6], 0)
        if rq[7] != 1:
            res.violate(rises[i], "request park bit", rq[7], 1)
        owner("host", i, i + 8, "request")
        head = "%s %s 0x%X" % ("AP" if apndp else "DP", "R" if rnw else "W", addr)
        if i + 12 > n:
            res.violate(rises[i], "truncated packet", "%d cycles" % (n - i), "12 cycles")
            break
        ack = _lsb(bit[i + 9:i + 12])
        name = ACKS.get(ack, "ACK%d" % ack)
        if ack != 7:
            owner("target", i + 9, i + 12, "ACK")
        if ack != 1:
            if ack == 7:
                res.violate(rises[i + 9], "no ACK", "111", "001, 010 or 100", text=head)
            elif ack not in ACKS:
                res.violate(rises[i + 9], "bad ACK", format(ack, "03b")[::-1], "001, 010 or 100", text=head)
            res.frame(rises[i], rises[i + 11], "%s %s" % (head, name))
            i += 13
            continue
        if i + 46 > n:
            res.violate(rises[i], "truncated data phase", "%d cycles" % (n - i), "46 cycles")
            break
        if rnw:
            bits, par = bit[i + 12:i + 44], bit[i + 44]
            owner("target", i + 12, i + 45, "read data")
        else:
            bits, par = bit[i + 13:i + 45], bit[i + 45]
            owner("host", i + 13, i + 46, "write data")
        data = _lsb(bits)
        if (sum(bits) + par) & 1:
            res.violate(rises[i + 44], "data parity", par, sum(bits) & 1, text=head)
        res.frame(rises[i], rises[i + 45], "%s OK %08X" % (head, data))
        i += 46
    return res.sort()


def _contention(res, host, target, trace, overlap):
    """Report every stretch where both sides drive SWDIO, if longer than overlap."""
    times = sorted(set(host.times) | set(target.times))
    start = None
    for t in times + [trace.end]:
        both = host.at(t) == 1 and target.at(t) == 1 and t < trace.end
        if both and start is None:
            start = t
        elif not both and start is not None:
            if trace.seconds(t - start) > overlap * (1 + 1e-9):
                res.violate(start, "bus contention", fmt_time(trace.seconds(t - start)),
                            "<= " + fmt_time(overlap), text="host and target both drive SWDIO")
            start = None


def _find_all(s, pat):
    out, k = set(), s.find(pat)
    while k >= 0:
        out.add(k)
        k = s.find(pat, k + 1)
    return out


def _wake_up(res, rises, bit, i):
    """Selection alert at i, then 4 low cycles and an activation code."""
    n = len(bit)
    j = i + 128
    code = _lsb(bit[j + 4:j + 12]) if j + 12 <= n else None
    if code == SWD_ACTIVATION and not any(bit[j:j + 4]):
        res.frame(rises[i], rises[j + 11], "DORMANT_TO_SWD")
        return j + 12
    res.frame(rises[i], rises[min(j, n) - 1], "SELECTION_ALERT",
              "activation code %s" % ("missing" if code is None else "0x%02X" % code))
    # some other protocol was selected: skip to the next line reset
    while j < n and not (j + 50 <= n and all(bit[j:j + 50])):
        j += 1
    return j


def _lsb(bits):
    v = 0
    for k, b in enumerate(bits):
        v |= b << k
    return v
