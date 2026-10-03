"""SWD judge: Serial Wire Debug packets from SWCLK and SWDIO.

Written from the ARM Debug Interface Architecture Specification (ADIv5),
serial wire protocol. SWDIO has a pull-up, so Z reads as 1. The host
writes SWDIO on the falling edge of SWCLK and the target reads it on the
rising edge; the target writes on the rising edge and the host reads on
the next rising edge. So every bit is read just before a rising edge: a
host bit at the edge that ends its cycle, a target bit launched at one
rising edge at the following rising edge. A target with a long output
delay (its bit appearing after the falling edge) is still read correctly,
as a host would read it.

A packet: 8 request bits from the host (Start=1, APnDP, RnW, A[2:3],
Parity even over the four, Stop=0, Park=1), one turnaround cycle, a 3-bit
ACK from the target (OK 001, WAIT 010, FAULT 100, sent least significant
first; 111 means nobody answered). After OK: a read has 32 data bits and
even parity from the target then a turnaround; a write has a turnaround
then 32 data bits and parity from the host. Data is least significant bit
first. At least 50 host cycles with SWDIO high form a line reset; the
16-bit JTAG-to-SWD select sequence 0xE79E after it is reported too.

Frame keys: "DP R 0x0 OK 2BA01477", "AP W 0xC WAIT", "LINE_RESET",
"JTAG_TO_SWD".
"""
from .common import Result

ROLES = ("swclk", "swdio")
REQUIRED = ("swclk", "swdio")
PULLUP = True
OPTS = ()
ACKS = {1: "OK", 2: "WAIT", 4: "FAULT", 7: "NOACK"}


def add_args(p):
    pass


def decode(trace):
    res = Result(trace)
    clk, dio = trace["swclk"], trace["swdio"]
    rises = clk.edges(-1, trace.end, 1)
    host = [1 if dio.before(r) != 0 else 0 for r in rises]
    # tgt[k] is the bit the target launched at rise k, read at rise k + 1
    tgt = host[1:] + [1 if dio.at(trace.end) != 0 else 0]
    n = len(rises)
    i = 0
    while i < n:
        if host[i] != 1:
            i += 1
            continue
        run = 0
        while i + run < n and host[i + run] == 1:
            run += 1
        if run >= 50:
            res.frame(rises[i], rises[i + run - 1], "LINE_RESET", "%d cycles" % run)
            i += run
            if i + 16 <= n and _lsb(host[i:i + 16]) == 0xE79E:
                res.frame(rises[i], rises[i + 15], "JTAG_TO_SWD")
                i += 16
            continue
        if i + 8 > n:
            res.violate(rises[i], "truncated request", "%d bits" % (n - i), "8 bits")
            break
        rq = host[i:i + 8]
        apndp, rnw, addr = rq[1], rq[2], (rq[3] | rq[4] << 1) << 2
        if (rq[1] + rq[2] + rq[3] + rq[4] + rq[5]) & 1:
            res.violate(rises[i], "request parity", rq[5], (rq[1] + rq[2] + rq[3] + rq[4]) & 1)
        if rq[6] != 0:
            res.violate(rises[i], "request stop bit", rq[6], 0)
        if rq[7] != 1:
            res.violate(rises[i], "request park bit", rq[7], 1)
        head = "%s %s 0x%X" % ("AP" if apndp else "DP", "R" if rnw else "W", addr)
        if i + 12 > n:
            res.violate(rises[i], "truncated packet", "%d cycles" % (n - i), "12 cycles")
            break
        ack = _lsb(tgt[i + 9:i + 12])
        name = ACKS.get(ack, "ACK%d" % ack)
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
            bits, par = tgt[i + 12:i + 44], tgt[i + 44]
        else:
            bits, par = host[i + 13:i + 45], host[i + 45]
        data = _lsb(bits)
        if (sum(bits) + par) & 1:
            res.violate(rises[i + 44], "data parity", par, sum(bits) & 1, text=head)
        res.frame(rises[i], rises[i + 45], "%s OK %08X" % (head, data))
        i += 46
    return res.sort()


def _lsb(bits):
    v = 0
    for k, b in enumerate(bits):
        v |= b << k
    return v
