# SPDX-License-Identifier: Apache-2.0
"""Unmodified OpenOCD reads a JTAG IDCODE through the chip's JTAG probe lane.

Lane 0 runs jtag_probe (specs/jtag_probe.tse): TCK, TMS and TDI on the uio
pads 0, 1 and 2, TDO read from uio[3]. Each host word is 16 (TMS, TDI)
pairs; the lane clocks them at 5 MHz and pushes the 16 TDO bits it sampled.
A JTAG TAP with a 4-bit IR, IDCODE 0x4BA00477 and BYPASS sits on those pads
as a bus model (the part on the board), changing TDO on falling TCK edges.

OpenOCD talks remote_bitbang to this script: pin writes ('0' to '7') and
TDO reads ('R'). Every rising TCK is one pair. Pairs are sent as soon as 16
are queued; when OpenOCD waits for TDO bits and fewer than 16 are queued,
the word is padded. Padding must not move the target away from where
OpenOCD believes it is, so the bridge tracks both TAP states and pads only
with clean moves (no capture, shift, update or reset): from Exit1 into
Pause, from Run-Test/Idle, Pause or Test-Logic-Reset in place. A scan that
waits in Shift has its last pair's TMS raised (the same bit still shifts)
and parks in Pause. The next pair OpenOCD sends is then reached from the
parked state by clean moves before it is clocked, with the same effect it
would have had (Pause, Exit2, Shift resumes a scan without an extra shift).

It prints 'rbb 127.0.0.1:PORT', serves one client and reports the words,
the pairs OpenOCD asked for and the pad pairs.
"""
import argparse
import os
import select
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import spi_bus as sb  # noqa: E402
import tse_demo as td  # noqa: E402

PADS = {"tck": 8, "tms": 9, "tdi": 10}         # uio[0..2]
TDO = 3                                         # uio[3], driven by the TAP model
PAIRS = 16
IDCODE = 0x4BA00477
IR_IDCODE, IR_BYPASS = 0xE, 0xF

TLR, RTI, SDS, SIS = "TLR", "RTI", "SelDR", "SelIR"
NEXT = {
    TLR: (RTI, TLR), RTI: (RTI, SDS), SDS: ("CapDR", SIS), SIS: ("CapIR", TLR),
    "CapDR": ("ShDR", "Ex1DR"), "ShDR": ("ShDR", "Ex1DR"), "Ex1DR": ("PaDR", "UpDR"),
    "PaDR": ("PaDR", "Ex2DR"), "Ex2DR": ("ShDR", "UpDR"), "UpDR": (RTI, SDS),
    "CapIR": ("ShIR", "Ex1IR"), "ShIR": ("ShIR", "Ex1IR"), "Ex1IR": ("PaIR", "UpIR"),
    "PaIR": ("PaIR", "Ex2IR"), "Ex2IR": ("ShIR", "UpIR"), "UpIR": (RTI, SDS),
}


def effect(x, y):
    """What the TCK cycle from state x to y does to the target: a shift or
    capture happens in x, an update or a reset on entering y."""
    if x.startswith(("Sh", "Cap")):
        return x
    if y.startswith("Up") or (y == TLR and x != TLR):
        return y
    return None


class Tap:
    """IEEE 1149.1 TAP on the probe's pads, a DrivenPins bus model."""

    def __init__(self):
        self.mask = 1 << 0 | 1 << 1 | 1 << 2
        self.state, self.ir, self.sh, self.n = TLR, IR_IDCODE, 0, 0
        self.tck = 0
        self.edges = 0

    def next_due(self):
        return None

    def update(self, pins, out, oe):
        tck = out & oe & 1
        tms = out >> 1 & 1 if oe >> 1 & 1 else 1
        tdi = out >> 2 & 1 if oe >> 2 & 1 else 1
        rise, fall = tck and not self.tck, self.tck and not tck
        self.tck = tck
        if rise:
            self.edges += 1
            s = self.state
            if s in ("ShDR", "ShIR"):
                self.sh = self.sh >> 1 | tdi << (self.n - 1)
            elif s == "CapDR":
                self.sh, self.n = (IDCODE, 32) if self.ir == IR_IDCODE else (0, 1)
            elif s == "CapIR":
                self.sh, self.n = 0b0001, 4
            self.state = NEXT[s][tms]
            if self.state == "UpIR":
                self.ir = self.sh & 0xF
            elif self.state == TLR:
                self.ir = IR_IDCODE
        elif fall:
            v = self.sh & 1 if self.state in ("ShDR", "ShIR") else 1
            pins.own[td.UIO][TDO] = v
            pins._push()


class Probe:
    """Pairs in, TDO bits out, one mailbox word of 16 pairs at a time."""

    def __init__(self, chip):
        self.chip = chip
        self.mb = sb.Mailbox(chip, 0)
        self.prev = self.actual = self.believed = TLR
        self.word = []                # (tms, tdi, real pair index or None)
        self.tdo = {}                   # real pair index -> TDO bit
        self.words = self.pads = 0

    def _emit(self, tms, tdi, idx):
        self.prev, self.actual = self.actual, NEXT[self.actual][tms]
        self.word.append((tms, tdi, idx))
        if len(self.word) == PAIRS:
            self._send()

    def _send(self):
        cmd = 0
        for i, (tms, tdi, _) in enumerate(self.word):
            cmd |= (tms | tdi << 1) << (2 * i)
        while self.mb.status() & self.mb.tx_full:
            self.chip.wait(20)
        self.chip.poke(self.mb.tx, cmd)
        while not self.mb.status() & self.mb.rx_full:
            self.chip.wait(20)
        cap = self.chip.peek(self.mb.rx)
        for i, (_, _, idx) in enumerate(self.word):
            if idx is None:
                self.pads += 1
            else:
                self.tdo[idx] = cap >> (PAIRS - 1 - i) & 1
        self.words += 1
        self.word = []

    def _route(self, tms):
        """Clean moves from the actual state, then the TMS for a cycle that
        reaches the state OpenOCD expects with the effect it expects."""
        want = NEXT[self.believed][tms]
        eff = effect(self.believed, want)
        seen, todo = {self.actual: []}, [self.actual]
        while todo:
            s = todo.pop(0)
            for t in (0, 1):
                if NEXT[s][t] == want and effect(s, want) == eff:
                    return seen[s], t
            for t in (0, 1):
                n = NEXT[s][t]
                if n not in seen and effect(s, n) is None:
                    seen[n] = seen[s] + [t]
                    todo.append(n)
        raise RuntimeError("no clean route from %s to %s" % (self.actual, want))

    def pair(self, tms, tdi, idx):
        path, t = self._route(tms)
        for p in path:
            self._emit(p, 1, None)
        self.believed = NEXT[self.believed][tms]
        self._emit(t, tdi, idx)

    def _walk(self, start, n):
        """n clean TMS values from start, back to start when they can be
        (leaving Test-Logic-Reset is clean, going back is a reset), or None."""
        ways = {start: []}
        for _ in range(n):
            nxt = {}
            for s, w in ways.items():
                for t in (0, 1):
                    if effect(s, NEXT[s][t]) is None:
                        nxt.setdefault(NEXT[s][t], w + [t])
            ways = nxt
        return ways.get(start, next(iter(ways.values()), None))

    def flush(self):
        """Send a partial word: pad it with clean moves."""
        if not self.word:
            return
        n = PAIRS - len(self.word)
        walk = self._walk(self.actual, n)
        if walk is None and n and self.actual in ("ShDR", "ShIR"):
            tms, tdi, idx = self.word[-1]
            ex1 = NEXT[self.prev][1]
            if effect(self.prev, ex1) == effect(self.prev, self.actual):
                self.word[-1] = (1, tdi, idx)       # the same shift or capture, then Exit1
                self.actual = ex1
                walk = self._walk(self.actual, n)
        if walk is None:
            raise RuntimeError("cannot pad a word in %s" % self.actual)
        for t in walk:
            self._emit(t, 1, None)


def serve(probe, srv):
    """remote_bitbang until 'Q' or the client closes."""
    conn, _ = srv.accept()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    tck, tms, tdi = 0, 1, 1
    npairs = 0
    reads = []                          # real pair index per outstanding 'R'
    ops = 0
    done = False
    while not done:
        # OpenOCD waits for TDO bits only after the clock that follows the
        # last 'R'; a read whose clock has not come yet is a write still in
        # flight, so wait longer for it.
        if reads and not select.select([conn], [], [], 0.005 if max(reads) < npairs else 2)[0]:
            last = max(reads)
            if last == npairs:          # a read with no clock after it: one in place
                hold = {TLR: 1, RTI: 0, "PaDR": 0, "PaIR": 0}.get(probe.believed)
                if hold is None:
                    raise RuntimeError("TDO read in %s without a clock" % probe.believed)
                probe.pair(hold, 1, npairs)
                npairs += 1
            probe.flush()
            conn.sendall(bytes(ord("0") + probe.tdo[i] for i in reads))
            reads = []
            continue
        data = conn.recv(65536)
        if not data:
            break
        for c in data:
            ops += 1
            if 0x30 <= c <= 0x37:
                v = c - 0x30
                ntck, tms, tdi = v >> 2 & 1, v >> 1 & 1, v & 1
                if ntck and not tck:
                    probe.pair(tms, tdi, npairs)
                    npairs += 1
                tck = ntck
            elif c == ord("R"):
                reads.append(npairs - 1 if tck else npairs)
            elif c == ord("Q"):
                done = True
                break
    probe.flush()
    conn.close()
    return ops, npairs


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--tsec", nargs=2, required=True, metavar=("DIR", "NAME"))
    a = ap.parse_args()
    lanes = td.read_tsec(*a.tsec)
    blocks = td.build_blocks(lanes, PADS, {"tdo": (td.UIO, TDO)})
    chip = td.Chip(a.sim)
    crc = chip.load(blocks)
    st = td.status_fields(chip.start(1))
    tap = Tap()
    chip.pins.hold(td.UIO, TDO, 1)
    chip.pins.attach(tap)
    chip.pipe.vcd_start()
    run_at = chip.pins.cycle
    print("loaded: crc 0x%08X; run: lane 0 from cycle %d, STATUS %s" % (crc, run_at, st))

    probe = Probe(chip)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    print("rbb 127.0.0.1:%d" % srv.getsockname()[1], flush=True)
    ops, npairs = serve(probe, srv)
    chip.wait(200)
    irq = chip.peek(td.chip_reg("IRQ"))
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    end = chip.pins.cycle
    chip.close()
    print("end: cycle %d (%.3f ms of chip time after RUN), %d remote_bitbang ops, %d pairs from "
          "OpenOCD, %d words, %d pad pairs, %d TCK edges at the TAP, IRQ 0x%04X, STATUS %s"
          % (end, (end - run_at) * 1e3 / td.FCLK, ops, npairs, probe.words, probe.pads,
             tap.edges, irq, st))
    bad = []
    if tap.edges != PAIRS * probe.words:
        bad.append("%d TCK edges for %d words" % (tap.edges, probe.words))
    if irq:
        bad.append("interrupt bits 0x%04X" % irq)
    if st["host_err"] or st["halted"] or st["running"] != 1:
        bad.append("host error, halted or stopped lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: every word clocked 16 pairs")
    return 0


if __name__ == "__main__":
    sys.exit(main())
