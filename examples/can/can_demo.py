# SPDX-License-Identifier: Apache-2.0
"""CAN receiver with in-bit ACK on the Verilated chip, against a CAN
transmitter model on a wired-AND bus.

The lane runs can_rx_ack: its txd is the chip's uio[7] pad, its rxd is
ui[6]. The bus here is the wired AND of the transmitter and the chip's txd
(dominant 0 wins), fed back into ui[6], so the chip hears its own ACK as a
real transceiver does. The transmitter is written apart from the chip's
units: it builds each base frame (CRC-15, stuffing from SOF to the end of
the CRC, then the unstuffed tail), drives one bit every FCLK / bitrate
cycles and samples the ACK slot at its sample point.

The first frame has a wrong CRC: the chip must leave the ACK slot
recessive and push nothing. Every later frame must be acknowledged and
pushed as three host words, which are decoded and compared with what was
sent. The run window printed at the end starts after the bad frame, so the
judge sees only the good ones.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tse_demo as td  # noqa: E402
from tse_host import image  # noqa: E402

TXD, RXD = 7, 6                     # uio bit (out), ui bit (in)
PADS = {"txd": 8 + TXD}
SAMPLE = 0.8
GAP_BITS = 40                       # recessive bits before each frame
POLL = 200

FRAMES = [
    (0x123, bytes.fromhex("DEADBEEF")),
    (0x000, b""),
    (0x7FF, bytes(range(1, 9))),
    (0x0F0, bytes.fromhex("0000000000")),
    (0x555, bytes.fromhex("FF")),
    (0x3E1, bytes.fromhex("A5A5A5A5A5A5A5")),
    (0x008, bytes.fromhex("4224")),     # a stuff bit after the last CRC bit
    (0x00E, bytes.fromhex("11")),       # CRC ends in 1111: the delimiter makes five ones
]


def crc15(bits):
    crc = 0
    for b in bits:
        nxt = b ^ (crc >> 14 & 1)
        crc = crc << 1 & 0x7FFF
        if nxt:
            crc ^= 0x4599
    return crc


def bits_of(v, n):
    return [v >> (n - 1 - i) & 1 for i in range(n)]


def frame_bits(ident, data, bad_crc=False):
    """(wire bits, index of the ACK slot): stuffed SOF..CRC, then the tail."""
    head = [0] + bits_of(ident, 11) + [0, 0, 0] + bits_of(len(data), 4)
    for d in data:
        head += bits_of(d, 8)
    crc = crc15(head) ^ (1 if bad_crc else 0)
    raw = head + bits_of(crc, 15)
    out, run, last = [], 0, None
    for b in raw:
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == 5:
            out.append(1 - b)
            last, run = 1 - b, 1
    ack = len(out) + 1
    return out + [1, 1, 1] + [1] * 7, ack


class Transmitter:
    """Drives a frame bit by bit and samples the bus in its ACK slot."""

    def __init__(self, bit):
        self.T = bit
        self.bits, self.ack, self.t0 = [], None, None
        self.level = 1
        self.acked = None
        self.end = 0

    def send(self, start, ident, data, bad_crc=False):
        self.bits, self.ack = frame_bits(ident, data, bad_crc)
        self.t0, self.k, self.acked = start, 0, None
        self.end = start + len(self.bits) * self.T

    def _edge(self, k):
        return self.t0 + k * self.T

    start_of = _edge

    def next_due(self):
        if self.t0 is None:
            return None
        if self.k < len(self.bits):
            nxt = self._edge(self.k)
            if self.acked is None and self.k == self.ack + 1:
                return min(nxt, self._edge(self.ack) + int(SAMPLE * self.T))
            return nxt
        return None

    def step(self, cycle, bus):
        if self.t0 is None:
            return
        if self.acked is None and self.k > self.ack and cycle >= self._edge(self.ack) + int(SAMPLE * self.T):
            self.acked = bus == 0
        while self.k < len(self.bits) and cycle >= self._edge(self.k):
            self.level = self.bits[self.k]
            self.k += 1
        if self.k >= len(self.bits) and cycle >= self.end:
            self.level = 1


class Bus:
    mask = 1 << TXD

    def __init__(self, tx):
        self.tx = tx
        self.chip_low = 0
        self.drives = []            # [cycle the chip pulled the bus low, cycle it let go]

    def next_due(self):
        return self.tx.next_due()

    def update(self, pins, out, oe):
        low = int(oe >> TXD & 1 and not out >> TXD & 1)
        if low and not self.chip_low:
            self.drives.append([pins.cycle, None])
        elif self.chip_low and not low:
            self.drives[-1][1] = pins.cycle
        self.chip_low = low
        self.tx.step(pins.cycle, self.level())
        lvl = self.level()
        pins.own[td.UI][RXD] = lvl
        pins._push()

    def level(self):
        return self.tx.level & (1 - self.chip_low)


def decode(words):
    """(id, dlc, data) from the three pushed words: the header (SOF in bit 18, the DLC in
    bits 3..0), then the data right-aligned in the other two."""
    h, w1, w2 = words
    h &= 0x7FFFF                    # the header word's upper bits are left over
    ident = h >> 7 & 0x7FF
    dlc = min(h & 0xF, 8)
    both = w1 << 32 | w2
    return ident, dlc, bytes((both >> (8 * (dlc - 1 - i))) & 0xFF for i in range(dlc))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--tsec", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--image", required=True)
    ap.add_argument("--bitrate", type=float, default=500e3)
    a = ap.parse_args()

    bit = td.FCLK / a.bitrate
    if bit != int(bit):
        raise SystemExit("bit time %.3f cycles is not whole" % bit)
    bit = int(bit)
    lanes = td.read_tsec(*a.tsec)
    lane = 0
    with open(a.image, "wb") as fh:
        fh.write(image.pack(td.build_blocks(lanes, PADS, {"rxd": (td.UI, RXD)}), td.FCLK, a.tsec[1]))
    with open(a.image, "rb") as fh:
        blocks = image.unpack(fh.read())[2]

    chip = td.Chip(a.sim)
    crc = chip.load(blocks)
    chip.pins.hold(td.UI, RXD, 1)   # after the load: the byte bus uses every ui bit
    print("loaded: %s, crc 0x%08X, %d blocks" % (a.image, crc, len(blocks)), flush=True)
    st = td.status_fields(chip.start(1 << lane))
    print("run: lane %d from cycle %d, bit %d cycles" % (lane, chip.pins.cycle, bit), flush=True)
    tx = Transmitter(bit)
    bus = Bus(tx)
    chip.pins.attach(bus)
    st_reg = td.chip_reg("MBOX_STATUS")
    rx_reg, rx_full = td.chip_reg("MBOX_RX", lane), 1 << (2 * lane + 1)

    def words_until(cycle, want=None):
        """Host words pushed before cycle, or until want words came (cycle is then a limit)."""
        got = []
        while chip.pins.cycle < cycle and (want is None or len(got) < want):
            if chip.peek(st_reg) & rx_full:
                got.append(chip.peek(rx_reg))
            else:
                chip.wait(POLL)
        return got

    bad = []
    plan = [(0x2AA, bytes.fromhex("0102"), True)] + [(i, d, False) for i, d in FRAMES]
    window = None
    ack_at, ack_len = [], []
    for n, (ident, data, bad_crc) in enumerate(plan):
        start = chip.pins.cycle + GAP_BITS * bit
        if n == 1:
            window = start - 20 * bit
        tx.send(start, ident, data, bad_crc)
        bus.drives = []
        got = words_until(tx.end + 30 * bit) if bad_crc else words_until(tx.end + 400 * bit, 3)
        got += words_until(chip.pins.cycle + 2 * bit)
        want = "%03X#%s" % (ident, data.hex().upper())
        if bad_crc:
            print("frame %d %s, wrong CRC: ACK %s, %d words pushed"
                  % (n, want, "dominant" if tx.acked else "recessive", len(got)), flush=True)
            if tx.acked or got:
                bad.append("frame with a wrong CRC was acknowledged or pushed")
            continue
        words = " ".join("%08X" % w for w in got)
        slot = tx.start_of(tx.ack)
        drv = ", ".join("+%d for %s" % (a - slot, "?" if b is None else b - a) for a, b in bus.drives)
        if len(bus.drives) != 1 or bus.drives[0][1] is None or not 0 <= bus.drives[0][0] - slot <= bit // 20:
            bad.append("frame %d: chip drove the bus at %s cycles from the ACK slot" % (n, drv or "no time"))
        else:
            ack_at.append(bus.drives[0][0] - slot)
            ack_len.append(bus.drives[0][1] - bus.drives[0][0])
        if len(got) != 3:
            print("frame %d %s: ACK %s, words %s" % (n, want, tx.acked, words), flush=True)
            bad.append("frame %d: %d words pushed" % (n, len(got)))
            continue
        ident2, dlc, data2 = decode(got)
        key = "%03X#%s" % (ident2, data2.hex().upper())
        print("frame %d %s: ACK %s, host %s (words %s)"
              % (n, want, "dominant" if tx.acked else "recessive", key, words), flush=True)
        if not tx.acked:
            bad.append("frame %d not acknowledged" % n)
        if key != want:
            bad.append("frame %d: host got %s" % (n, key))
    chip.wait(20 * bit)
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    chip.close()
    print("end: cycle %d, STATUS %s" % (chip.pins.cycle, st))
    print("window: %.9f s" % (window / td.FCLK))
    if st["host_err"] or st["halted"]:
        bad.append("host error or halted lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: %d frames acknowledged and pushed, 1 wrong-CRC frame ignored; ACK driven %d to %d "
          "cycles into the slot (first time quantum: %d), for %d to %d cycles (bit: %d)"
          % (len(FRAMES), min(ack_at), max(ack_at), bit // 20, min(ack_len), max(ack_len), bit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
