# SPDX-License-Identifier: Apache-2.0
"""I2C target (Fast or Standard mode) on the Verilated chip, answering a
simulated controller.

The lane runs i2c_target_fm or i2c_target_sm at address 0x50 with its
open-drain pads on uio[2] (SCL) and uio[3] (SDA). The bus is a wired AND of
the chip, the controller model and the pull-up, resolved every time the
chip moves a line or the controller has something to do; a released line
reads high TR clocks later. The controller model keeps every Standard- or
Fast-mode minimum with a little to spare and counts each SCL high period
from the rise it observes, so the lane's clock stretching holds it.

The host keeps the target's registers and answers every push while the
lane holds SCL low: 0x0002 (addressed for a write) with any word, 0x01nn
(byte nn written; the first one is the register pointer) with 0 to ACK or 1
to NACK, 0x0003 (send a byte) with the register at the pointer. The
controller writes 7F 3C from register 0x10, addresses 0x51, which must stay
unanswered, reads the two bytes back with a repeated START, and writes to
read-only register 0x80, which the host NACKs.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import tse_demo as td  # noqa: E402

SCL, SDA = 2, 3                     # uio bits
PADS = {"scl": 8 + SCL, "sda": 8 + SDA}
INPUTS = {"scl": (td.UIO, SCL), "sda": (td.UIO, SDA)}
TR = 12                             # bus: clocks from release to a high level (200 ns)
POLL = 100

# Controller timing in 60 MHz clocks: (SDA hold after SCL falls, SCL low,
# SCL high from the observed rise, tHD;STA, tSU;STA, tSU;STO, tBUF).
SPEEDS = {
    "fm": dict(hold=18, low=90, high=60, hd_sta=45, su_sta=45, su_sto=45, buf=90),
    "sm": dict(hold=30, low=300, high=300, hd_sta=270, su_sta=300, su_sto=270, buf=300),
}

READ_ONLY = 0x80                    # host: a byte written at or above this register is NACKed
DATA = [0x7F, 0x3C]
TXNS = [
    [("S",), ("W", 0xA0), ("W", 0x10)] + [("W", d) for d in DATA],
    [("S",), ("W", 0xA2), ("W", 0x00)],
    [("S",), ("W", 0xA0), ("W", 0x10), ("Sr",), ("W", 0xA1), ("R", True), ("R", False)],
    [("S",), ("W", 0xA0), ("W", READ_ONLY), ("W", 0x55), ("W", 0x66)],
]                                   # each ends with STOP, early after a NACK
ACKS = [0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 1]     # ACK bits the controller sees, in order
PUSHES = [0x002, 0x110, 0x17F, 0x13C, 0x002, 0x110, 0x003, 0x003, 0x002, 0x180, 0x155]


class Controller:
    """A generator per bus transaction list. It yields a number of clocks to
    wait, or 'rise' to wait until SCL reads high; it pulls a line by setting
    pull[line] = 1."""

    def __init__(self, bus, t, txns):
        self.b, self.t = bus, t
        self.acks, self.got = [], []
        self.run = self.program(txns)

    def program(self, txns):
        t, p = self.t, self.b.pull
        for txn in txns:
            for op in txn:
                if op[0] == "S":                    # from the idle bus
                    p["sda"] = 1
                    yield t["hd_sta"]
                    p["scl"] = 1
                elif op[0] == "Sr":                 # from SCL low after an ACK slot
                    yield t["hold"]
                    p["sda"] = 0
                    yield t["low"] - t["hold"]
                    p["scl"] = 0
                    yield "rise"
                    yield t["su_sta"]
                    p["sda"] = 1
                    yield t["hd_sta"]
                    p["scl"] = 1
                elif op[0] == "W":
                    for i in range(7, -1, -1):
                        yield from self.bit_out(op[1] >> i & 1)
                    a = yield from self.bit_in()
                    self.acks.append(a)
                    if a:                           # NACK: STOP next
                        break
                elif op[0] == "R":
                    v = 0
                    for _ in range(8):
                        v = v << 1 | (yield from self.bit_in())
                    self.got.append(v)
                    yield from self.bit_out(0 if op[1] else 1)
            yield t["hold"]                         # STOP, from SCL low
            p["sda"] = 1
            yield t["low"] - t["hold"]
            p["scl"] = 0
            yield "rise"
            yield t["su_sto"]
            p["sda"] = 0
            yield t["buf"]

    def bit_out(self, v):
        t, p = self.t, self.b.pull
        yield t["hold"]
        p["sda"] = 0 if v else 1
        yield t["low"] - t["hold"]
        p["scl"] = 0
        yield "rise"
        yield t["high"]
        p["scl"] = 1

    def bit_in(self):
        t, p = self.t, self.b.pull
        yield t["hold"]
        p["sda"] = 0
        yield t["low"] - t["hold"]
        p["scl"] = 0
        yield "rise"
        yield t["high"] // 2
        v = self.b.lvl["sda"]
        yield t["high"] - t["high"] // 2
        p["scl"] = 1
        return v


class Bus:
    """Wired AND of chip, controller and pull-up on uio[2] and uio[3]."""

    mask = 1 << SCL | 1 << SDA

    def __init__(self, t, txns, tr):
        self.tr = tr
        self.pull = {"scl": 0, "sda": 0}
        self.lvl = {"scl": 1, "sda": 1}
        self.rise = {"scl": None, "sda": None}
        self.ctl = Controller(self, t, txns)
        self.wake, self.want_rise, self.done = 0, False, False

    def next_due(self):
        due = [c for c in self.rise.values() if c is not None]
        if not self.done and not self.want_rise:
            due.append(self.wake)
        return min(due) if due else None

    def _resolve(self, chip, cycle):
        for k in chip:
            if not chip[k] or self.pull[k]:
                self.lvl[k], self.rise[k] = 0, None
            elif not self.lvl[k]:
                if self.rise[k] is None:
                    self.rise[k] = cycle + self.tr
                if cycle >= self.rise[k]:
                    self.lvl[k], self.rise[k] = 1, None

    def update(self, pins, out, oe):
        cycle = pins.cycle
        chip = {"scl": not (oe >> SCL & 1 and not out >> SCL & 1),
                "sda": not (oe >> SDA & 1 and not out >> SDA & 1)}
        self._resolve(chip, cycle)
        while not self.done:
            ready = self.lvl["scl"] if self.want_rise else cycle >= self.wake
            if not ready:
                break
            try:
                w = next(self.ctl.run)
            except StopIteration:
                self.done = True
                break
            self.want_rise = w == "rise"
            if not self.want_rise:
                self.wake = cycle + w
            self._resolve(chip, cycle)
        pins.own[td.UIO][SCL] = self.lvl["scl"]
        pins.own[td.UIO][SDA] = self.lvl["sda"]
        pins._push()


class TargetHost:
    """The host side of the target lane: registers, pointer, answers."""

    def __init__(self, lane):
        self.tx = td.chip_reg("MBOX_TX", lane)
        self.rx = td.chip_reg("MBOX_RX", lane)
        self.tx_full, self.rx_full = 1 << (2 * lane), 1 << (2 * lane + 1)
        self.regs = {}
        self.ptr = 0
        self.first = False
        self.log = []

    def poll(self, chip, st):
        if not st & self.rx_full:
            return False
        v = chip.peek(self.rx) & 0xFFFF
        self.log.append(v)
        answer = 0                      # 0x0002: any word; a written byte: 0 = ACK
        if v == 2:
            self.first = True
        elif v == 3:
            b = self.regs.get(self.ptr, 0xFF)
            self.ptr = (self.ptr + 1) & 0xFF
            answer = int("{:08b}".format(b)[::-1], 2)
        elif v >> 8 == 1:
            if self.first:
                self.ptr, self.first = v & 0xFF, False
            elif self.ptr >= READ_ONLY:
                answer = 1              # NACK
            else:
                self.regs[self.ptr] = v & 0xFF
                self.ptr = (self.ptr + 1) & 0xFF
        chip.poke(self.tx, answer)
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--tsec", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--speed", choices=sorted(SPEEDS), required=True)
    ap.add_argument("--tr", type=int, default=TR, help="bus rise time, clocks")
    ap.add_argument("--limit", type=int, default=600000, help="cycles the transactions may take")
    a = ap.parse_args()

    lanes = td.read_tsec(*a.tsec)
    chip = td.Chip(a.sim)
    chip.pins.hold(td.UIO, SCL, 1)
    chip.pins.hold(td.UIO, SDA, 1)
    crc = chip.load(td.build_blocks(lanes, PADS, INPUTS))
    st = td.status_fields(chip.start(1))
    run_at = chip.pins.cycle
    print("loaded: crc 0x%08X; run: lane 0 from cycle %d, STATUS %s" % (crc, run_at, st), flush=True)
    host = TargetHost(0)
    bus = Bus(SPEEDS[a.speed], TXNS, a.tr)
    chip.pins.attach(bus)
    st_reg = td.chip_reg("MBOX_STATUS")
    while not bus.done:
        if chip.pins.cycle > run_at + a.limit:
            raise SystemExit("FAIL host: timed out at cycle %d" % chip.pins.cycle)
        if not host.poll(chip, chip.peek(st_reg)):
            chip.wait(POLL)
    chip.wait(200)
    while host.poll(chip, chip.peek(st_reg)):
        pass
    irq = chip.peek(td.chip_reg("IRQ"))
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    chip.close()

    ctl = bus.ctl
    print("controller: acks %s, read %s" % ("".join(map(str, ctl.acks)), bytes(ctl.got).hex()))
    print("host: pushes %s, registers 0x10.. %s"
          % (" ".join("%03x" % v for v in host.log), bytes(host.regs.get(0x10 + i, 0) for i in range(2)).hex()))
    print("end: cycle %d, IRQ 0x%04X, STATUS %s" % (chip.pins.cycle, irq, st))
    print("window: %.9f s" % (run_at / td.FCLK))
    bad = []
    if ctl.acks != ACKS:
        bad.append("ACK bits %s, expected %s" % ("".join(map(str, ctl.acks)), "".join(map(str, ACKS))))
    if ctl.got != DATA or [host.regs.get(0x10 + i) for i in range(2)] != DATA:
        bad.append("data differ")
    if host.log != PUSHES:
        bad.append("pushes differ")
    if host.regs.get(READ_ONLY) is not None:
        bad.append("the read-only register was written")
    if irq or st["host_err"] or st["halted"]:
        bad.append("interrupt, host error or halted lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: %d bytes written, %d read back with a repeated START, %d pushes answered, "
          "%d NACKs (address 0x51, read-only register)" % (len(DATA), len(ctl.got), len(host.log),
                                                           ctl.acks.count(1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
