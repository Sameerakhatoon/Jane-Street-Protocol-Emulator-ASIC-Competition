"""I2C controller (Fast or Standard mode, the image decides) on the
Verilated chip, writing to and reading from a simulated target.

The chip drives SCL and SDA as open-drain pads on uio[2] and uio[3]. Every
model cycle this script reads the chip's uio outputs over the pin pipe,
resolves each line as a wired AND of the chip, the target and the pull-up,
and feeds the bus back into uio_in, so the chip and the target see the
same bus one cycle later (well inside the 300 ns rise time the spec
allows). The target is a register device at address 0x50: the first byte
written sets its register pointer, later bytes are stored, reads return
registers from the pointer. It holds SCL low for STRETCH cycles after the
ACK of the second byte of a write, so the controller must wait for the rise
it observes. It NACKs a byte written to a register at READ_ONLY or above,
and nothing answers at 0x51, so two transactions end in a NACK that the host
handles by sending STOP at once.

The host sends the controller one command per step over the TX mailbox
(the word bit-reversed, as the tsec image contract says) and reads the ACK
bit or the received byte from the RX mailbox after each byte. Checks here
are the host's view: every byte ACKed, the bytes read back, the target's
registers. The judge checks the bus from the VCD.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tse_demo as td  # noqa: E402
from tse_host import image  # noqa: E402

SCL, SDA = 2, 3                     # uio bits
PADS = {"scl": 8 + SCL, "sda": 8 + SDA}
OP_START, OP_RESTART, OP_WRITE, OP_READ_ACK, OP_READ_NACK, OP_STOP = 1, 2, 3, 4, 5, 6
HOLD = 20                           # target: clocks from an SCL fall to its SDA change
STRETCH = 300                       # target: clocks SCL is held low once (5 us)
TR = 12                             # bus: clocks from release to a high level (200 ns)
POLL = 200
READ_ONLY = 0x80                    # target: a byte written at or above this register is NACKed


def bitrev16(v):
    return int("{:016b}".format(v & 0xFFFF)[::-1], 2)


class Target:
    def __init__(self, addr):
        self.addr = addr
        self.regs = {}
        self.ptr = None
        self.state = "idle"
        self.pull = {"scl": 0, "sda": 0}
        self.later = []             # (cycle, line, pull)
        self.prev = (1, 1)
        self.n = self.sh = 0
        self.nbytes = 0
        self.stretched = False

    def _at(self, cycle, line, pull):
        self.later.append((cycle, line, pull))

    def _send(self, cycle):
        b = self.regs.get(self.ptr, 0xFF)
        self.ptr = (self.ptr + 1) & 0xFF
        self.out = [(b >> (7 - i)) & 1 for i in range(8)]
        self._at(cycle + HOLD, "sda", 1 - self.out[0])
        self.n = 1
        self.state = "rdata"

    def step(self, cycle, scl, sda):
        for c, line, p in [x for x in self.later if x[0] <= cycle]:
            self.pull[line] = p
        self.later = [x for x in self.later if x[0] > cycle]
        pscl, psda = self.prev
        self.prev = (scl, sda)
        if scl and pscl and psda and not sda:
            self.state, self.n, self.sh = "addr", 0, 0
            self.pull["sda"] = 0
            return
        if scl and pscl and not psda and sda:
            self.state = "idle"
            self.pull["sda"] = 0
            return
        if scl and not pscl:
            if self.state in ("addr", "wdata"):
                self.sh = self.sh << 1 | sda
                self.n += 1
            elif self.state == "ackin":
                self.acked = sda == 0
        elif pscl and not scl:
            self._fall(cycle)

    def _fall(self, cycle):
        s = self.state
        if s in ("addr", "wdata") and self.n == 8:
            if s == "addr":
                if self.sh >> 1 != self.addr:
                    self.state = "idle"
                    return
                self.next = "rdata" if self.sh & 1 else "wdata"
                self.nbytes = 0
            else:
                if self.nbytes == 0:
                    self.ptr = self.sh
                elif self.ptr >= READ_ONLY:
                    self.state = "nackout"      # leave SDA released: NACK
                    return
                else:
                    self.regs[self.ptr] = self.sh
                    self.ptr = (self.ptr + 1) & 0xFF
                self.nbytes += 1
                self.next = "wdata"
            self._at(cycle + HOLD, "sda", 1)
            self.state = "ackout"
        elif s == "nackout":
            self.state = "idle"             # a STOP or a repeated START follows
        elif s == "ackout":
            self._at(cycle + HOLD, "sda", 0)
            if self.next == "wdata" and self.nbytes == 2 and not self.stretched:
                self.stretched = True
                self.pull["scl"] = 1
                self._at(cycle + STRETCH, "scl", 0)
            if self.next == "rdata":
                self._send(cycle)
            else:
                self.state, self.n, self.sh = "wdata", 0, 0
        elif s == "rdata":
            if self.n == 8:
                self._at(cycle + HOLD, "sda", 0)
                self.state = "ackin"
            else:
                self._at(cycle + HOLD, "sda", 1 - self.out[self.n])
                self.n += 1
        elif s == "ackin":
            if self.acked:
                self._send(cycle)
            else:
                self.state = "idle"


class Bus:
    """Wired AND of chip, target and pull-up on uio[2] and uio[3]."""

    mask = 1 << SCL | 1 << SDA

    def __init__(self, target, tr):
        self.t = target
        self.tr = tr
        self.lvl = {"scl": 1, "sda": 1}
        self.rise = {"scl": None, "sda": None}

    def next_due(self):
        due = [c for c, _, _ in self.t.later] + [c for c in self.rise.values() if c is not None]
        return min(due) if due else None

    def _resolve(self, chip, cycle):
        """A pulled line falls at once; a released one reads high tr clocks later."""
        for k in chip:
            if not chip[k] or self.t.pull[k]:
                self.lvl[k], self.rise[k] = 0, None
            elif not self.lvl[k]:
                if self.rise[k] is None:
                    self.rise[k] = cycle + self.tr
                if cycle >= self.rise[k]:
                    self.lvl[k], self.rise[k] = 1, None
        return dict(self.lvl)

    def update(self, pins, out, oe):
        chip = {"scl": not (oe >> SCL & 1 and not out >> SCL & 1),
                "sda": not (oe >> SDA & 1 and not out >> SDA & 1)}
        seen = None
        lvl = self._resolve(chip, pins.cycle)
        while lvl != seen:              # the target also sees its own release of a line
            seen = lvl
            self.t.step(pins.cycle, lvl["scl"], lvl["sda"])
            lvl = self._resolve(chip, pins.cycle)
        pins.own[td.UIO][SCL] = lvl["scl"]
        pins.own[td.UIO][SDA] = lvl["sda"]
        pins._push()


DATA = [0xDE, 0xAD, 0xBE, 0xEF]
WRITE = [(OP_START, 0), (OP_WRITE, 0xA0), (OP_WRITE, 0x10)] + [(OP_WRITE, d) for d in DATA] + [(OP_STOP, 0)]
READ = [(OP_START, 0), (OP_WRITE, 0xA0), (OP_WRITE, 0x10), (OP_RESTART, 0), (OP_WRITE, 0xA1),
        (OP_READ_ACK, 0), (OP_READ_ACK, 0), (OP_READ_ACK, 0), (OP_READ_NACK, 0), (OP_STOP, 0)]
# NACKs: no target at 0x51, and a byte written to a read-only register. The
# host drops the rest of a transaction after a NACK and sends STOP.
NO_TARGET = [(OP_START, 0), (OP_WRITE, 0xA2), (OP_WRITE, 0x00), (OP_STOP, 0)]
READ_ONLY_WRITE = [(OP_START, 0), (OP_WRITE, 0xA0), (OP_WRITE, READ_ONLY), (OP_WRITE, 0x55),
                   (OP_WRITE, 0x66), (OP_STOP, 0)]
OPS = WRITE + NO_TARGET + READ + READ_ONLY_WRITE
ACKS = [0] * 6 + [1] + [0] * 3 + [0, 0, 1]      # ACK bits pushed by the lane, in order


class I2cJob:
    """Feeds controller commands to a lane and collects what it pushes back.
    poll() does at most one SPI access, given the MBOX_STATUS word."""

    def __init__(self, lane, ops, acks=None, dropped=0):
        """acks: the ACK bits expected in order (default: every write ACKed);
        dropped: the commands the host should drop after NACKs."""
        self.lane = lane
        self.ops = list(ops)
        self.want_acks = acks if acks is not None else [0] * sum(op == OP_WRITE for op, _ in ops)
        self.want_dropped = dropped
        self.tx = td.chip_reg("MBOX_TX", lane)
        self.rx = td.chip_reg("MBOX_RX", lane)
        self.tx_full, self.rx_full = 1 << (2 * lane), 1 << (2 * lane + 1)
        self.wait_push = None
        self.acks, self.got = [], []
        self.dropped = 0

    def done(self):
        return not self.ops and self.wait_push is None

    def poll(self, chip, st):
        if self.wait_push is not None:
            if not st & self.rx_full:
                return False
            v = chip.peek(self.rx)
            if self.wait_push == OP_WRITE:
                self.acks.append(v & 1)
                while v & 1 and self.ops[0][0] != OP_STOP:  # NACK: straight to STOP
                    self.ops.pop(0)
                    self.dropped += 1
            else:
                self.got.append(v & 0xFF)
            self.wait_push = None
            return True
        if self.ops and not st & self.tx_full:
            op, d = self.ops.pop(0)
            chip.poke(self.tx, bitrev16(d << 8 | op))
            if op in (OP_WRITE, OP_READ_ACK, OP_READ_NACK):
                self.wait_push = op
            return True
        return False

    def problems(self, target):
        regs = [target.regs.get(0x10 + i) for i in range(len(DATA))]
        print("i2c: acks %s, read %s, target registers 0x10.. %s"
              % ("".join(str(a) for a in self.acks), bytes(self.got).hex(), bytes(r or 0 for r in regs).hex()))
        bad = []
        if self.acks != self.want_acks:
            bad.append("ACK bits %s, expected %s"
                       % ("".join(map(str, self.acks)), "".join(map(str, self.want_acks))))
        if self.dropped != self.want_dropped:
            bad.append("%d commands dropped after a NACK, expected %d" % (self.dropped, self.want_dropped))
        if self.got != DATA or regs != DATA:
            bad.append("data differ")
        if target.regs.get(READ_ONLY) is not None:
            bad.append("the read-only register was written")
        return bad


def run_jobs(chip, jobs, limit):
    st_reg = td.chip_reg("MBOX_STATUS")
    while not all(j.done() for j in jobs):
        if chip.pins.cycle > limit:
            raise SystemExit("FAIL host: timed out at cycle %d" % chip.pins.cycle)
        st = chip.peek(st_reg)
        if not any([j.poll(chip, st) for j in jobs]):
            chip.wait(POLL)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--tsec", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--image", required=True)
    ap.add_argument("--tr", type=int, default=TR, help="bus rise time, clocks")
    ap.add_argument("--limit", type=int, default=600000, help="cycles the transactions may take")
    a = ap.parse_args()

    lanes = td.read_tsec(*a.tsec)
    lane = 0
    with open(a.image, "wb") as fh:
        fh.write(image.pack(td.build_blocks(lanes, PADS, {"scl": (td.UIO, SCL), "sda": (td.UIO, SDA)}),
                            td.FCLK, a.tsec[1]))
    with open(a.image, "rb") as fh:
        blocks = image.unpack(fh.read())[2]

    target = Target(0x50)
    job = I2cJob(lane, OPS, ACKS, 2)
    chip = td.Chip(a.sim)
    chip.pins.hold(td.UIO, SCL, 1)
    chip.pins.hold(td.UIO, SDA, 1)
    crc = chip.load(blocks)
    print("loaded: %s, crc 0x%08X, %d blocks" % (a.image, crc, len(blocks)), flush=True)
    st = td.status_fields(chip.start(1 << lane))
    run_at = chip.pins.cycle
    print("run: lane %d from cycle %d, STATUS %s" % (lane, run_at, st), flush=True)
    chip.pins.attach(Bus(target, a.tr))
    run_jobs(chip, [job], run_at + a.limit)
    chip.wait(600)
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    chip.close()

    bad = job.problems(target)
    print("end: cycle %d, STATUS %s" % (chip.pins.cycle, st))
    print("window: %.9f s" % (run_at / td.FCLK))
    if st["host_err"] or st["halted"]:
        bad.append("host error or halted lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: %d bytes written, %d read back, %d ACKs, %d NACKs handled"
          % (len(DATA), len(job.got), job.acks.count(0), job.acks.count(1)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
