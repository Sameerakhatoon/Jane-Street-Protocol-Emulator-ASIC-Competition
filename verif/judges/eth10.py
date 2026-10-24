"""10BASE-T judge: Ethernet frames at the bit level from the transmit line.

Written from IEEE 802.3: Manchester coding as in the physical signalling
clause (first half of each cell the complement of the bit, second half the
bit, 10 Mb/s +/- 0.01 percent), the frame format and FCS of the MAC
clause. The line is the logic level of TD+ (a digital model of the
differential pair); link pulses are not judged.

End of frame (TP_IDL, the 10BASE-T clause): after the last transition of
a frame the transmitter holds TD positive for at least 250 ns, then
returns to idle, within 45 bit times (4.5 us). On the logic level that is
a high of 250 ns to 4.5 us measured from the last low-to-high transition:
the middle transition of a final 1, or the cell boundary rise after a
final 0. The judge checks that width for every decoded frame; a frame
whose line drops straight back to low has no TP_IDL and fails. The fall
that ends a valid TP_IDL is not data and starts no burst.

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
from array import array

from .common import Result, fmt_time
from .manchester import bursts
from .vcd import Signal

ROLES = ("td",)
REQUIRED = ("td",)
PULLUP = False
OPTS = ("rate_tol", "idl_min", "idl_max")

BITRATE = 10e6
PREAMBLE_SFD_BITS = 64
MIN_OCTETS = 64
MAX_OCTETS = 1518
MAX_TAGGED = 1522
IDL_MIN = 250e-9
IDL_MAX = 4.5e-6


def add_args(p):
    p.add_argument("--rate-tol", type=float, default=0.01, help="allowed bit rate error, percent")
    p.add_argument("--idl-min", type=float, default=IDL_MIN, metavar="SECONDS",
                   help="shortest TP_IDL high after the last transition (default 250 ns)")
    p.add_argument("--idl-max", type=float, default=IDL_MAX, metavar="SECONDS",
                   help="latest return to idle after the last transition (default 4.5 us)")


def crc32(octets):
    crc = 0xFFFFFFFF
    for o in octets:
        for k in range(8):
            b = (o >> k) & 1
            crc = (crc >> 1) ^ 0xEDB88320 if (crc ^ b) & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF


def decode(trace, rate_tol=0.01, idl_min=IDL_MIN, idl_max=IDL_MAX):
    res = Result(trace)
    T = trace.units(1.0 / BITRATE)
    td = trace["td"]
    for b in bursts(res, _without_idle_ends(td, T), T, "ieee", rate_tol, 3.0, nominal=BITRATE):
        _frame(res, b)
        if b.bits and not b.error:
            _tp_idl(res, trace, td, b, idl_min, idl_max)
    return res.sort()


def _without_idle_ends(td, T):
    """The line with the falling edge that ends each TP_IDL removed.

    That fall follows a high stretch longer than any Manchester cell can
    hold (1.5 bit times) and is followed by idle (no edge for 3 bit
    times), so it is never a data edge; left in, it would look like the
    start of a burst of its own.
    """
    times, levels = td.times, td.levels
    n = len(times)
    keep = []
    for k in range(n):
        if (k >= 2 and levels[k] == 0 and levels[k - 1] == 1 and times[k] - times[k - 1] > 1.5 * T
                and (k + 1 == n or times[k + 1] - times[k] > 3.0 * T)):
            continue
        keep.append(k)
    s = Signal(td.name)
    s.times = array("q", (times[k] for k in keep))
    s.levels = bytearray(levels[k] for k in keep)
    return s


def _tp_idl(res, trace, td, b, idl_min, idl_max):
    """The positive idle after the frame's last transition."""
    if td.at(b.t1) != 1:
        res.violate(b.t1, "TP_IDL", "missing (line low after the last bit)",
                    ">= %s high" % fmt_time(idl_min))
        return
    k = td.first_after(b.t1)
    if k < len(td.times):
        res.check_range(b.t1, "TP_IDL", td.times[k] - b.t1, idl_min, idl_max,
                        text="high after the last transition")
    elif trace.seconds(trace.end - b.t1) > idl_max * (1 + 1e-9):
        res.violate(b.t1, "TP_IDL", "high to the end of the trace", "back to idle within %s" % fmt_time(idl_max))


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
