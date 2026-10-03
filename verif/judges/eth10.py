"""10BASE-T judge: Ethernet frames at the bit level from the transmit line.

Written from IEEE 802.3: Manchester coding as in the physical signalling
clause (first half of each cell the complement of the bit, second half the
bit, 10 Mb/s +/- 0.01 percent), the frame format and FCS of the MAC
clause. The line is the logic level of TD+ (a digital model of the
differential pair); link pulses are not judged.

A frame is one Manchester burst: 7 octets of preamble (10101010 on the
wire) and the start frame delimiter 10101011, then destination, source,
length or type, data and the 32-bit FCS, every octet least significant
bit first. The FCS is the CRC-32 with generator 0x04C11DB7 over the
octets after the SFD, started from all ones and sent complemented; the
judge recomputes it bit by bit. A frame is 64 to 1518 octets from
destination to FCS (1522 with a VLAN tag).

Frame keys: "FFFFFFFFFFFF 020000000001 0800 4500...", destination,
source, type and payload in hex (FCS left out); "ERROR" when the burst
does not decode.
"""
from .common import Result
from .manchester import bursts

ROLES = ("td",)
REQUIRED = ("td",)
PULLUP = False
OPTS = ("rate_tol",)

BITRATE = 10e6
PREAMBLE_SFD_BITS = 64
MIN_OCTETS = 64
MAX_OCTETS = 1518
MAX_TAGGED = 1522


def add_args(p):
    p.add_argument("--rate-tol", type=float, default=0.01, help="allowed bit rate error, percent")


def crc32(octets):
    crc = 0xFFFFFFFF
    for o in octets:
        for k in range(8):
            b = (o >> k) & 1
            crc = (crc >> 1) ^ 0xEDB88320 if (crc ^ b) & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


def decode(trace, rate_tol=0.01):
    res = Result(trace)
    T = trace.units(1.0 / BITRATE)
    for b in bursts(res, trace["td"], T, "ieee", rate_tol, 3.0, nominal=BITRATE):
        _frame(res, b)
    return res.sort()


def _frame(res, b):
    bits = b.bits
    if b.error:
        res.frame(b.t0, b.t1, "ERROR", "Manchester")
        return
    k = 0
    while k < len(bits) and bits[k] == (k & 1) ^ 1:
        k += 1
    # k stops at the second 1 of the SFD's final "11"
    if k < 2 or k >= len(bits) or bits[k] != 1 or bits[k - 1] != 1:
        res.violate(b.t0, "SFD", "not found", "10101011 after the preamble")
        res.frame(b.t0, b.t1, "ERROR", "no SFD")
        return
    n_pre = k + 1
    if n_pre != PREAMBLE_SFD_BITS:
        res.violate(b.t0, "preamble", "%d bits" % n_pre, "%d bits with the SFD" % PREAMBLE_SFD_BITS)
    data = bits[n_pre:]
    if len(data) % 8:
        res.violate(b.t1, "alignment", "%d extra bits" % (len(data) % 8), "whole octets")
    octets = []
    for j in range(len(data) // 8):
        v = 0
        for i, x in enumerate(data[8 * j:8 * j + 8]):
            v |= x << i
        octets.append(v)
    if len(octets) < 18:
        res.violate(b.t0, "frame size", "%d octets" % len(octets), ">= %d octets" % MIN_OCTETS)
        res.frame(b.t0, b.t1, "ERROR", "runt")
        return
    tagged = octets[12] == 0x81 and octets[13] == 0x00
    lim = MAX_TAGGED if tagged else MAX_OCTETS
    if len(octets) < MIN_OCTETS or len(octets) > lim:
        res.violate(b.t0, "frame size", "%d octets" % len(octets), "%d to %d octets" % (MIN_OCTETS, lim))
    body = octets[:-4]
    got = octets[-4] | octets[-3] << 8 | octets[-2] << 16 | octets[-1] << 24
    want = crc32(body)
    if got != want:
        res.violate(b.t0, "FCS", "0x%08X" % got, "0x%08X" % want)
    hx = "".join("%02X" % o for o in body)
    res.frame(b.t0, b.t1, "%s %s %s %s" % (hx[0:12], hx[12:24], hx[24:28], hx[28:]),
              "%d octets" % len(octets))
