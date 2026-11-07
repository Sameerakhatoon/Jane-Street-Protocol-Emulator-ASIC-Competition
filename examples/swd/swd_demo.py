# SPDX-License-Identifier: Apache-2.0
"""Unmodified OpenOCD reads an SWD DPIDR through the chip's SWD probe lane.

Lane 0 runs swd_probe_driven.tse (specs/swd_probe.tse with SWDIO driven and
released explicitly) built for SWCLK 1 MHz with an 80 ns edge tolerance:
SWCLK on the uio[0] pad, SWDIO on uio[1] (the lane turns the pad around). An SW-DP model sits on those pads as a bus model,
the same register set as sim/rtl/sim_swd_dp.sv: DPIDR, ABORT, CTRL/STAT
with the power-up acknowledges, SELECT and RDBUFF; it answers OK only.

OpenOCD talks remote_bitbang SWD to this script: 'O' and 'o' turn its
SWDIO driver on and off, 'd' to 'g' set SWCLK and SWDIO, 'c' reads SWDIO.
Every rising SWCLK is one bit. The probe lane does whole steps (the host
interface of the spec), so the script cuts OpenOCD's bits into them:

  host driving, then 'o'   the last 8 bits are a request: step 1 (request,
                           turnaround, 3 ACK bits); bits before them go out
                           as steps 5 (16 bits) and 6 (1 bit)
  target driving           bit 0 turnaround, 1 to 3 the ACK (step 1); a read
                           then has 16 + 16 data bits (step 2 twice), parity
                           and a turnaround (step 3); a write one turnaround
                           (step 4)
  host driving again       the write data and parity and idle cycles: steps
                           5 and 6

OpenOCD reads the turnaround bits too; nobody drives the line then and
the lane does not return them, so the script answers 1, the pulled-up
level. Every other bit OpenOCD reads is one the lane sampled.
"""
import argparse
import os
import socket
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import spi_bus as sb  # noqa: E402
import tse_demo as td  # noqa: E402

PADS = {"swclk": 8, "swdio": 9}                 # uio[0], uio[1]
SWDIO = 1
DPIDR = 0x2BA01477


def bitrev(v, n):
    return sb.bitrev(v, n)


class SwDp:
    """SW-DP on the probe's pads, a DrivenPins bus model. SWDIO is taken on
    a rising SWCLK edge and driven right after one."""

    def __init__(self):
        self.mask = 1 << 0 | 1 << SWDIO
        self.clk = 1
        self.st, self.ones, self.n, self.req, self.sr = "LOCK", 0, 0, 0, 0
        self.ctrl = self.sel = 0
        self.oe, self.out = 0, 1
        self.edges = 0
        self.clashes = 0

    def next_due(self):
        return None

    def _reg(self, a):
        if a == 0x0:
            return DPIDR
        if a == 0x4:
            return self.ctrl | (self.ctrl >> 30 & 1) << 31 | (self.ctrl >> 28 & 1) << 29
        if a == 0xC:
            return self.sel
        return 0

    def update(self, pins, out, oe):
        host_oe = oe >> SWDIO & 1
        if host_oe and self.oe:
            self.clashes += 1
        clk = out & oe & 1 if oe & 1 else 1
        rise = clk and not self.clk
        self.clk = clk
        if not rise:
            return
        self.edges += 1
        b = (out >> SWDIO & 1) if host_oe else (self.out if self.oe else 1)
        n, ones = self.n, self.ones
        self.n, self.ones = n + 1, ones + 1 if b else 0
        st = self.st
        if b and ones >= 49 and not self.oe:
            self.st = "RESET"
        elif st == "RESET":
            if not b:
                self.st = "IDLE"
        elif st == "IDLE":
            if b:
                self.st, self.req, self.n = "REQ", 1, 1
        elif st == "REQ":                               # LSB first: start .. park
            self.req |= b << n
            if n == 7:
                r = self.req
                par = bin(r >> 1 & 0xF).count("1") & 1
                good = r & 1 and not r >> 6 & 1 and r >> 7 & 1 and not r >> 1 & 1 and (r >> 5 & 1) == par
                self.st, self.n = ("ACK", 0) if good else ("LOCK", n + 1)
        elif st == "ACK":                               # after the turnaround, OK = 1, 0, 0
            self.oe, self.out = 1, int(n == 0)
            if n == 2:
                self.n = 0
                if self.req >> 2 & 1:
                    v = self._reg((self.req >> 3 & 3) << 2)
                    self.st, self.sr = "RDATA", v | (bin(v).count("1") & 1) << 32
                else:
                    self.st = "WTRN"
        elif st == "RDATA":                             # 32 data bits, parity, release
            self.out, self.sr = self.sr & 1, self.sr >> 1
            if n == 33:
                self.oe, self.out, self.st = 0, 1, "RTRN"
        elif st == "RTRN":                              # the host's turnaround clock
            self.st = "IDLE"
        elif st == "WTRN":                              # release, one turnaround clock
            self.oe, self.out = 0, 1
            if n == 1:
                self.st, self.n, self.sr = "WDATA", 0, 0
        elif st == "WDATA":                             # 32 data bits and parity from the host
            self.sr |= b << n
            if n == 32:
                self.st = "IDLE"
                if bin(self.sr).count("1") & 1 == 0:
                    a = (self.req >> 3 & 3) << 2
                    if a == 0x4:
                        self.ctrl = self.sr & 0xFFFFFFFF
                    elif a == 0x8:
                        self.sel = self.sr & 0xFFFFFFFF
        pins.own[td.UIO][SWDIO] = self.out if self.oe else 1
        pins._push()


class Probe:
    """The probe lane's host steps over the mailbox."""

    def __init__(self, chip):
        self.chip = chip
        self.mb = sb.Mailbox(chip, 0)
        self.steps = {}
        self.clocks = 0

    def _put(self, w):
        while self.mb.status() & self.mb.tx_full:
            self.chip.wait(20)
        self.chip.poke(self.mb.tx, w)

    def _get(self):
        while not self.mb.status() & self.mb.rx_full:
            self.chip.wait(20)
        return self.chip.peek(self.mb.rx)

    def step(self, op, arg=None, nbits=0):
        self.steps[op] = self.steps.get(op, 0) + 1
        self.clocks += {1: 12, 2: 16, 3: 2, 4: 1, 5: 16, 6: 1}[op]
        self._put(op)
        if arg is not None:
            self._put(arg)
        if nbits:                       # first bit received highest
            return bitrev(self._get(), nbits)
        return None

    def out(self, bits):
        """Host bits, LSB first in time: steps 5 and 6."""
        while len(bits) >= 16:
            self.step(5, sum(b << i for i, b in enumerate(bits[:16])))
            bits = bits[16:]
        for b in bits:
            self.step(6, b)


def serve(probe, srv):
    """remote_bitbang SWD until 'Q' or the client closes; the bit counts."""
    conn, _ = srv.accept()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    drive, clk, dio = 1, 1, 1
    outq = []                           # host bits not yet sent
    inbits = None                       # target segment: bit values the lane returned, by index
    nin = 0                             # rising edges in this target segment
    rnw = 0
    ops = reads = 0

    def need(i):
        """The lane's value of target bit i, running the step that holds it."""
        if i not in inbits:
            if rnw and 4 <= i < 20:
                v = probe.step(2, nbits=16)
                inbits.update({4 + k: v >> k & 1 for k in range(16)})
            elif rnw and 20 <= i < 36:
                v = probe.step(2, nbits=16)
                inbits.update({20 + k: v >> k & 1 for k in range(16)})
            elif rnw and i in (36, 37):
                inbits.update({36: probe.step(3, nbits=1), 37: 1})
            elif not rnw and i == 4:
                probe.step(4)
                inbits[4] = 1
            else:
                raise RuntimeError("target bit %d is not in a step (%s)" % (i, "read" if rnw else "write"))
        return inbits[i]

    while True:
        data = conn.recv(65536)
        if not data:
            break
        reply = bytearray()
        for c in data:
            ops += 1
            ch = chr(c)
            if "d" <= ch <= "g":
                v = c - ord("d")
                nclk, dio = v >> 1 & 1, v & 1
                if nclk and not clk:
                    if drive:
                        outq.append(dio)
                    else:
                        nin += 1
                clk = nclk
            elif ch == "o" and drive:
                if len(outq) < 8:
                    raise RuntimeError("target turn after %d host bits" % len(outq))
                probe.out(outq[:-8])
                req = sum(b << i for i, b in enumerate(outq[-8:]))
                outq = []
                rnw = req >> 2 & 1
                ack = probe.step(1, req, nbits=3)
                inbits = {0: 1, 1: ack & 1, 2: ack >> 1 & 1, 3: ack >> 2 & 1}
                nin = 0
                drive = 0
            elif ch == "O" and not drive:
                for i in range(nin):
                    need(i)
                drive = 1
            elif ch == "c":
                reads += 1
                if drive:
                    raise RuntimeError("SWDIO read while the host drives")
                reply.append(ord("0") + need(nin))
            elif ch == "Q":
                conn.sendall(reply)
                probe.out(outq)
                conn.close()
                return ops, reads
        if reply:
            conn.sendall(reply)
    probe.out(outq)
    conn.close()
    return ops, reads


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--tsec", nargs=2, required=True, metavar=("DIR", "NAME"))
    a = ap.parse_args()
    lanes = td.read_tsec(*a.tsec)
    # tsec sets idle_oe (PAD_OUT bit 8) for every push_pull wire, so the pad
    # would keep driving its idle level while the lane has SWDIO turned
    # around; an inout wire must let go, which the crossbar does with
    # idle_oe 0. This is the compiler's configuration, corrected here.
    pin = lanes[0][3]["swdio"]
    pin["PAD_OUT"] = "0x%08X" % (int(pin["PAD_OUT"], 16) & ~(1 << 8))
    blocks = td.build_blocks(lanes, PADS, {"swdio": (td.UIO, SWDIO)})
    chip = td.Chip(a.sim)
    crc = chip.load(blocks)
    st = td.status_fields(chip.start(1))
    dp = SwDp()
    chip.pins.hold(td.UIO, SWDIO, 1)
    chip.pins.attach(dp)
    chip.pipe.vcd_start()
    run_at = chip.pins.cycle
    print("loaded: crc 0x%08X; run: lane 0 from cycle %d, STATUS %s" % (crc, run_at, st))

    probe = Probe(chip)
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    print("rbb 127.0.0.1:%d" % srv.getsockname()[1], flush=True)
    ops, reads = serve(probe, srv)
    chip.wait(200)
    irq = chip.peek(td.chip_reg("IRQ"))
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    end = chip.pins.cycle
    chip.close()
    print("end: cycle %d (%.3f ms of chip time after RUN), %d remote_bitbang ops, %d SWDIO reads, "
          "steps %s, %d SWCLK edges at the SW-DP, IRQ 0x%04X, STATUS %s"
          % (end, (end - run_at) * 1e3 / td.FCLK, ops, reads,
             " ".join("%d:%d" % kv for kv in sorted(probe.steps.items())), dp.edges, irq, st))
    bad = []
    if dp.edges != probe.clocks:
        bad.append("%d SWCLK edges for %d step clocks" % (dp.edges, probe.clocks))
    if dp.clashes:
        bad.append("%d updates with both sides driving SWDIO" % dp.clashes)
    if irq:
        bad.append("interrupt bits 0x%04X" % irq)
    if st["host_err"] or st["halted"] or st["running"] != 1:
        bad.append("host error, halted or stopped lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: every step clocked as the spec says, no SWDIO clash")
    return 0


if __name__ == "__main__":
    sys.exit(main())
