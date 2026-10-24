"""cocotb: tse_prog_mem on its own, with stub lanes and a stub host.

The stubs drive the lane fetch and data ports exactly as the port list in
tse_prog_mem describes (request held until the grant, data the cycle
after), so the chip side of LDW, STW, the fallback store and the host turn is
tested before the lanes issue LDW and STW. A shadow memory in grant order is
the reference for every word read.
"""
import os
import random

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, ReadOnly, RisingEdge

LANES = int(os.environ.get("LANES", "2"))
NW = 16                                        # data window words the random test uses


def clock(dut):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())


def dslot(lane, c):
    """First data cycle of lane at or after cycle c (absolute cycles, cnt = c mod 16)."""
    s = c
    while s % 16 != 4 * lane + 3:
        s += 1
    return s


class Bench:
    """One coroutine drives every port; ops are queued per lane and for the host."""

    def __init__(self, dut, seed=1):
        self.dut = dut
        self.rng = random.Random(seed)
        self.cyc = 0
        self.mem = {}                          # SRAM word -> value (window 3 is the 3-lane data window)
        self.fb = [0] * 8
        self.lane_ops = [[] for _ in range(LANES)]
        self.host_ops = []
        self.fetch = [None] * LANES            # (window, word, fallback) a lane fetches every slot
        self.stopped = 0
        self.log = []                          # (cycle, who, kind, addr, value)
        self.errors = []
        self.h_hold = 0                        # h_wdata holds two cycles after a write grant

    async def start(self):
        d = self.dut
        await FallingEdge(d.clk)
        for name in ("l_req", "l_addr", "l_win", "l_fb", "d_req", "d_we", "d_addr", "d_wdata",
                     "h_req", "h_we", "h_dw", "h_fb", "h_addr", "h_wdata",
                     "t_en", "t_req", "t_we", "t_addr", "t_wdata", "all_stopped"):
            getattr(d, name).value = 0
        d.rst_n.value = 0
        d.clr.value = 1
        await ClockCycles(d.clk, 3)
        d.rst_n.value = 1
        await ClockCycles(d.clk, 2)
        d.clr.value = 0
        await ClockCycles(d.clk, 2)
        await RisingEdge(d.clk)
        await ReadOnly()
        self.cyc = int(d.cnt.value)            # absolute cycles start in phase with cnt

    def dw_read(self, a):
        """Expected word, or None for a word never written (random in silicon)."""
        return self.mem.get(0x180 + a)

    def dw_write(self, a, v):
        self.mem[0x180 + a] = v

    async def run(self, cycles):
        d = self.dut
        pend_l = [None] * LANES                # lane data read waiting for d_rdata: expected value
        pend_f = None                          # (lane, expected) fetch word due this cycle
        pend_h = None
        for _ in range(cycles):
            await RisingEdge(d.clk)
            self.cyc += 1
            # drive this cycle
            dreq = dwe = 0
            daddr = dwdata = 0
            cur = [None] * LANES
            for i in range(LANES):
                q = self.lane_ops[i]
                if q and q[0]["at"] <= self.cyc:
                    op = q[0]
                    op.setdefault("c", self.cyc)
                    cur[i] = op
                    dreq |= 1 << i
                    dwe |= op["we"] << i
                    daddr |= op["addr"] << (7 * i)
                    dwdata |= op.get("data", 0) << (32 * i)
            d.d_req.value = dreq
            d.d_we.value = dwe
            d.d_addr.value = daddr
            d.d_wdata.value = dwdata
            lreq = laddr = lwin = lfb = 0
            for i, f in enumerate(self.fetch):
                if f is not None:
                    w, a, fb = f
                    lreq |= 1 << i
                    laddr |= a << (7 * i)
                    lwin |= w << (2 * i)
                    lfb |= fb << i
            d.l_req.value = lreq
            d.l_addr.value = laddr
            d.l_win.value = lwin
            d.l_fb.value = lfb
            h = None
            if self.host_ops and self.host_ops[0]["at"] <= self.cyc and self.cyc > self.h_hold:
                h = self.host_ops[0]
            d.h_req.value = int(h is not None)
            if h is not None:
                h.setdefault("c", self.cyc)
                d.h_we.value = h["we"]
                d.h_dw.value = int(h["kind"] == "dw")
                d.h_fb.value = int(h["kind"] == "fb")
                d.h_addr.value = h["addr"]
                if h["we"]:
                    d.h_wdata.value = h["data"]
            d.all_stopped.value = self.stopped
            await ReadOnly()
            # read data of last cycle's grants
            for i in range(LANES):
                if pend_l[i] is not None:
                    got = int(d.d_rdata.value)
                    if got != pend_l[i]:
                        self.errors.append("cycle %d lane %d LDW got %08x want %08x" % (self.cyc, i, got, pend_l[i]))
                    pend_l[i] = None
            if pend_f is not None:
                got = int(d.l_rdata.value)
                if got != pend_f[1]:
                    self.errors.append("cycle %d lane %d fetch got %08x want %08x" % (self.cyc, pend_f[0], got, pend_f[1]))
                pend_f = None
            if pend_h is not None:
                got = int(d.h_rdata.value)
                if got != pend_h:
                    self.errors.append("cycle %d host read got %08x want %08x" % (self.cyc, got, pend_h))
                pend_h = None
            # grants of this cycle, applied to the shadow in grant order
            dg = int(d.d_gnt.value)
            lg = int(d.l_gnt.value)
            for i in range(LANES):
                if (dg >> i) & 1:
                    op = cur[i]
                    if op is None:
                        self.errors.append("cycle %d lane %d data grant without request" % (self.cyc, i))
                        continue
                    if op["we"]:
                        self.dw_write(op["addr"], op["data"])
                    else:
                        pend_l[i] = self.dw_read(op["addr"])
                    self.log.append((self.cyc, i, op))
                    self.lane_ops[i].pop(0)
                if (lg >> i) & 1:
                    w, a, fb = self.fetch[i]
                    if self.cyc % 4 != i:
                        self.errors.append("cycle %d lane %d fetch grant off its slot" % (self.cyc, i))
                    if fb and int(d.u_sram.men.value):
                        self.errors.append("cycle %d fallback fetch enabled the SRAM" % self.cyc)
                    want = self.fb[a & 7] if fb else self.mem.get(w * 128 + a)
                    pend_f = None if want is None else (i, want)
            if h is not None and int(d.h_gnt.value):
                k, a = h["kind"], h["addr"]
                if h["we"]:
                    self.h_hold = self.cyc + 2
                    if k == "fb":
                        self.fb[a & 7] = h["data"]
                    elif k == "dw":
                        self.dw_write(a & 0x7F, h["data"])
                    else:
                        self.mem[a] = h["data"]
                else:
                    pend_h = self.fb[a & 7] if k == "fb" else self.dw_read(a & 0x7F) if k == "dw" else self.mem.get(a)
                self.log.append((self.cyc, "h", h))
                self.host_ops.pop(0)

    def check(self):
        assert not self.errors, "\n".join(self.errors[:10])


@cocotb.test()
async def test_01_data_slot_every_phase(dut):
    """LDW and STW from every lane dispatched in every phase of the 16-cycle frame are
    granted in the lane's first data cycle s >= c (STW lasts s + 1 - c, LDW s + 2 - c)
    and LDW returns what STW stored."""
    clock(dut)
    b = Bench(dut)
    await b.start()
    t = b.cyc + 4
    for lane in range(LANES):
        for ph in range(16):
            c = t + ((ph - t) % 16)
            a = (lane * 16 + ph) % 128
            b.lane_ops[lane].append({"at": c, "we": 1, "addr": a, "data": 0x1000 * lane + ph})
            b.lane_ops[lane].append({"at": c + 20, "we": 0, "addr": a})
            t = c + 40
    await b.run(t - b.cyc + 40)
    b.check()
    for cyc, who, op in b.log:
        if who == "h":
            continue
        s = dslot(who, op["c"])
        assert cyc == s, "lane %d dispatched %d granted %d, want %d" % (who, op["c"], cyc, s)
        dur = s + 1 - op["c"] if op["we"] else s + 2 - op["c"]
        assert (1 <= dur <= 16) if op["we"] else (2 <= dur <= 17)
    assert len([x for x in b.log if x[1] != "h"]) == 2 * 16 * LANES


async def lane_schedule(dut, others, seed):
    """Lane 0 does a fixed mix of fetches and data accesses; others add traffic or not."""
    b = Bench(dut, seed)
    await b.start()
    rng = random.Random(5)
    b.fetch[0] = (0, 3, 0)
    t = b.cyc + 2
    for k in range(40):
        t += rng.randint(1, 20)
        b.lane_ops[0].append({"at": t, "we": k % 2, "addr": rng.randrange(8), "data": k})
    if others:
        r2 = random.Random(seed)
        for i in range(1, LANES):
            b.fetch[i] = (i, 5, i % 2)
            for k in range(60):
                b.lane_ops[i].append({"at": b.cyc + 1 + k * 3, "we": r2.randrange(2), "addr": 8 + r2.randrange(16),
                                      "data": r2.getrandbits(32)})
        for k in range(30):
            b.host_ops.append({"at": b.cyc + k * 7, "we": r2.randrange(2), "kind": "dw", "addr": 8 + r2.randrange(16),
                               "data": r2.getrandbits(32)})
    await b.run(t - b.cyc + 700)
    b.check()
    return [(c, op["c"], op["we"], op["addr"]) for c, who, op in b.log if who == 0]


@cocotb.test()
async def test_02_non_interference(dut):
    """Lane 0's data grants are the same cycles whether the other lanes and the host
    hammer their ports or stay idle."""
    clock(dut)
    quiet = await lane_schedule(dut, False, 1)
    busy = await lane_schedule(dut, True, 2)
    assert quiet == busy
    assert len(quiet) == 40


@cocotb.test()
async def test_03_host_turn(dut):
    """Host data window access only at cnt == 15 while lanes run and in any cycle
    when all are stopped. Read data matches the shadow."""
    clock(dut)
    b = Bench(dut, 3)
    await b.start()
    rng = random.Random(3)
    for stopped in (1, 0):
        b.stopped = stopped
        n0 = len(b.log)
        for k in range(24):
            b.host_ops.append({"at": b.cyc + 1, "we": int(k < 12), "kind": "dw", "addr": rng.randrange(12),
                               "data": rng.getrandbits(32)})
        await b.run(500)
        cnts = [cyc % 16 for cyc, who, op in b.log[n0:]]
        ops = [op for cyc, who, op in b.log[n0:]]
        assert len(cnts) == 24
        for c, op in zip(cnts, ops):
            if not stopped:
                assert c == 15, "host at cnt %d while lanes run" % c
        if stopped:
            assert any(c != 15 for c in cnts)
    b.check()


@cocotb.test()
async def test_04_fallback_store(dut):
    """Host writes and reads the fallback store; a lane fetching from it gets word
    addr mod 8 in its own slot with the SRAM idle, also with a dead SRAM; a lane on
    the SRAM keeps getting its words beside it."""
    clock(dut)
    b = Bench(dut, 4)
    await b.start()
    b.stopped = 1
    rng = random.Random(4)
    for k in range(8):
        b.host_ops.append({"at": 0, "we": 1, "kind": "fb", "addr": k, "data": rng.getrandbits(32)})
    for k in range(4):
        b.host_ops.append({"at": 0, "we": 1, "kind": "sram", "addr": 0x80 + k, "data": rng.getrandbits(32)})
    for k in range(8):
        b.host_ops.append({"at": 0, "we": 0, "kind": "fb", "addr": k})
    await b.run(120)
    b.stopped = 0
    for a in range(12):
        b.fetch[0] = (2, a, 1)
        b.fetch[1] = (1, a % 4, 0)
        await b.run(9)
    await FallingEdge(dut.clk)
    dut.u_sram.u_macro.fault_kind.value = 6
    b.fetch[1] = None
    for a in range(8):
        b.fetch[0] = (0, a, 1)
        await b.run(9)
    await FallingEdge(dut.clk)
    dut.u_sram.u_macro.fault_kind.value = 0
    b.check()


@cocotb.test()
async def test_05_random_traffic(dut):
    """Random LDW, STW, fetches and host accesses from everyone at once agree with the
    shadow memory in grant order."""
    clock(dut)
    b = Bench(dut, 5)
    await b.start()
    rng = random.Random(55)
    b.stopped = 1
    for a in range(NW):
        b.host_ops.append({"at": 0, "we": 1, "kind": "dw", "addr": a, "data": rng.getrandbits(32)})
    await b.run(400)
    b.stopped = 0
    t = b.cyc
    for i in range(LANES):
        b.fetch[i] = (i, rng.randrange(128), 0)
        for k in range(50):
            b.lane_ops[i].append({"at": t + k * 6 + rng.randrange(6), "we": rng.randrange(2),
                                  "addr": rng.randrange(NW), "data": rng.getrandbits(32)})
    for k in range(60):
        b.host_ops.append({"at": t + k * 5, "we": rng.randrange(2), "kind": "dw", "addr": rng.randrange(NW),
                           "data": rng.getrandbits(32)})
    await b.run(1200)
    b.check()
    assert all(not q for q in b.lane_ops) and not b.host_ops
