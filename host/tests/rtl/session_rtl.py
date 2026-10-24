"""cocotb: the host library itself (Session over PinLink, the code the model tests
and the board run) against the whole chip.

Session is plain blocking code, so it runs in a worker thread and drives the pins
through ThreadPins, a pin bus whose set_inputs() and tick() are queued to the
cocotb coroutine and whose uo_out waits for the answer. The coroutine blocks on
the queue while the worker computes, so simulated time only moves on tick() and
the timing is exactly PinLink's.
"""
import queue
import threading

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, FallingEdge, RisingEdge

from tse_host import frames, model, regs_map
from tse_host.link import PinLink
from tse_host.session import Session

FB = regs_map.REGIONS["fallback"]["base"]
DW = regs_map.REGIONS["data"]["base"]
PROG_LO = {k: v[1] for k, v in regs_map.LANE["LANE_PROG"]["fields"].items()}

# SET PINDIRS 0x11; loop: SET PINS 0x11; SET X 0; SET PINS 0x10; JMP 1
PROG = [0x40114111, 0x40104200, 0x00010001]
PAD0 = (regs_map.CHIP["PAD_OUT"]["addr"], [0x1C0])      # pad 0 from lane 0 pin 0


def lane_prog(lane, window=0, fallback=0):
    return (0x1000 + 0x100 * lane + regs_map.LANE["LANE_PROG"]["addr"],
            [window << PROG_LO["window"] | fallback << PROG_LO["fallback"]])


MAIN = [(0x0000, PROG), lane_prog(0, 0), PAD0]
FALLBACK = [(FB, PROG), lane_prog(0, 0, 1), PAD0]


class ThreadPins:
    def __init__(self):
        self.req = queue.Queue()
        self.ans = queue.Queue()
        self.cycle = 0

    def set_inputs(self, ui_in=None, uio_in=None, rst_n=None):
        self.req.put(("set", (ui_in, uio_in, rst_n)))

    def tick(self, n=1):
        self.cycle += n
        self.req.put(("tick", n))

    @property
    def uo_out(self):
        self.req.put(("read", None))
        return self.ans.get()

    def fault(self, kind):
        """SRAM model fault class (0 none, 1 stuck-at-1 cell, 6 dead macro)."""
        self.req.put(("fault", kind))

    def trace(self, on):
        """While on, the coroutine notes every clock at which uo_out[0] changes;
        trace(False) returns those cycles (on the same count as self.cycle)."""
        self.req.put(("trace", on))
        return None if on else self.ans.get()


async def run_host(dut, fn):
    """Run fn(session, pins) in a worker thread against the chip; re-raise its error."""
    cocotb.start_soon(Clock(dut.clk, 16666, unit="ps").start())
    dut.ena.value = 1
    dut.ui_in.value = 0
    dut.uio_in.value = 0
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)
    pins = ThreadPins()
    out = {}
    cycle, trace = 0, None

    def work():
        try:
            out["value"] = fn(Session(PinLink(pins)), pins)
        except BaseException as e:      # handed back to the coroutine
            out["error"] = e
        pins.req.put(("end", None))

    threading.Thread(target=work, daemon=True).start()
    while True:
        op, arg = pins.req.get()
        if op == "end":
            break
        if op == "set":
            for sig, v in zip((dut.ui_in, dut.uio_in, dut.rst_n), arg):
                if v is not None:
                    sig.value = v
        elif op == "tick" and trace is None:
            await ClockCycles(dut.clk, arg)
            cycle += arg
        elif op == "tick":
            for _ in range(arg):
                await ClockCycles(dut.clk, 1)
                cycle += 1
                v = int(dut.uo_out.value) & 1
                if v != last:
                    trace.append(cycle)
                last = v
        elif op == "read":
            pins.ans.put(int(dut.uo_out.value))
        elif op == "fault":
            await FallingEdge(dut.clk)
            dut.u_top.u_pmem.u_sram.u_macro.fault_kind.value = arg
            await RisingEdge(dut.clk)
        elif op == "trace" and arg:
            trace, last = [], int(dut.uo_out.value) & 1
        elif op == "trace":
            pins.ans.put(trace)
            trace = None
    if "error" in out:
        raise out["error"]
    return out.get("value")


def pin0_changes(pins, n):
    """Cycles (pins.cycle) at which uo_out[0] changes during the next n clocks."""
    last, out = pins.uo_out & 1, []
    for _ in range(n):
        pins.tick(1)
        v = pins.uo_out & 1
        if v != last:
            out.append(pins.cycle)
        last = v
    return out


@cocotb.test()
async def test_01_load(dut):
    """Session.load with readback; the words read back over SPI in run mode; every
    chip configuration word lands in its {bank, word} slot, and the host reads it
    as 0 (the bank has no read port)."""
    cfg = [("PAD_OUT", 1, 0x1FF), ("LANE_IN", 2, 0x1FF), ("TRIG_WIN", 2, 0xFFFF)]
    blocks = [(regs_map.CHIP[n]["addr"] + i, [v]) for n, i, v in cfg]

    def fn(s, pins):
        pins.fault(0)
        s.load(MAIN + blocks)
        s.leave_load()
        return s.peek(0x0000, 3), [s.peek(a)[0] for a, _ in blocks]

    words, reads = await run_host(dut, fn)
    bank = int(dut.u_top.u_ccfg.q.value)
    assert words == PROG
    assert reads == [0, 0, 0]
    assert [bank >> 32 * model.bank_slot(n, i) & 0xFFFFFFFF for n, i, _ in cfg] == [v for *_, v in cfg]


@cocotb.test()
async def test_02_boot_bist_pass(dut):
    """boot(): the self-test passes in fewer clocks than the session waits, and the
    SRAM image is loaded; the model's self-test length is within 2 percent."""
    def fn(s, pins):
        pins.fault(0)
        s.enter_load()
        s.link.exchange(frames.prologue(True))
        t0 = pins.cycle
        while not s.stat()["bist_done"]:
            pins.tick(50)
        took = pins.cycle - t0
        return took, s.boot(MAIN, FALLBACK)

    took, rep = await run_host(dut, fn)
    dut._log.info("self-test done after %d clocks", took)
    assert took < 13000 and abs(took - model.BIST_CYCLES) <= model.BIST_CYCLES // 50
    assert rep["path"] == "sram" and rep["bist"] == 0


@cocotb.test()
async def test_03_boot_forced_fail(dut):
    """A dead macro (simulation fault hook) fails the self-test with the BIST value the
    model predicts; boot() loads the fallback image instead, and lane 0 runs from the
    fallback store: a steady square wave on uo_out[0]."""
    def fn(s, pins):
        pins.fault(6)
        rep = s.boot(MAIN, FALLBACK)
        st = s.start(1)
        return rep, st, pin0_changes(pins, 120)

    rep, st, edges = await run_host(dut, fn)
    await FallingEdge(dut.clk)
    dut.u_top.u_pmem.u_sram.u_macro.fault_kind.value = 0
    assert rep["path"] == "fallback" and rep["bist"] == model.BIST_DEAD, hex(rep["bist"])
    assert st >> 8 & 0xF == 1
    gaps = [b - a for a, b in zip(edges, edges[1:])][2:]
    assert len(edges) >= 8 and len(set(gaps[0::2])) == 1 and len(set(gaps[1::2])) == 1, edges


@cocotb.test()
async def test_04_cell_fault_report(dut):
    """A stuck-at-1 cell (word 77) gives the BIST value the model gives for a failing
    cell at that word."""
    def fn(s, pins):
        pins.fault(1)
        return s.selftest()

    res = await run_host(dut, fn)
    want = model.Chip(seed=0, bist_fail_word=77)
    want._bist_finish()
    assert res == want.bist_info, "chip 0x%04x, model 0x%04x" % (res, want.bist_info)


@cocotb.test()
async def test_05_data_window(dut):
    """The data window from the host while lane 0 runs: an alias of SRAM window 3,
    all 128 words."""
    data = [0x22220000 + i for i in range(8)]

    def fn(s, pins):
        pins.fault(0)
        s.load(MAIN + [(DW, data)])
        alias = s.peek(0x0180, 8)
        s.start(1)
        s.poke(DW + 3, 0xCAFEF00D)
        s.poke(DW + 15, 0x15151515)
        s.poke(DW + 127, 0x7F7F7F7F)
        return (alias, s.peek(DW, 4), s.peek(DW + 15, 1) + s.peek(DW + 127, 1),
                s.peek(0x01FF)[0], s.peek(regs_map.STATUS)[0])

    alias, low, high, top, st = await run_host(dut, fn)
    assert alias == data
    assert high == [0x15151515, 0x7F7F7F7F] and top == 0x7F7F7F7F
    assert low == data[:3] + [0xCAFEF00D]
    assert not st & 0x08 and st >> 8 & 0xF == 1


@cocotb.test()
async def test_06_run_timing(dut):
    """RUN acts at the end of the 16-cycle frame: whatever the phase of the write
    (16 different delays before the same SPI write), lane 0's first pin change, its
    start, comes at the same cycle modulo 16 from run-mode entry, and 1 to 16 cycles
    later than it would if RUN acted at once."""
    def fn(s, pins):
        pins.fault(0)
        starts = []
        for delay in range(16):
            s.load(MAIN, bist=False)
            s.leave_load()
            c0 = pins.cycle
            pins.tick(delay)
            pins.trace(True)
            s.poke(regs_map.RUN, 1)
            pins.tick(24)
            starts.append(pins.trace(False)[0] - c0 - delay)
        return starts

    starts = await run_host(dut, fn)
    dut._log.info("start after the write began, per delay: %s", starts)
    assert len(set((st + d) % 16 for d, st in enumerate(starts))) == 1
    assert max(starts) - min(starts) == 15


@cocotb.test()
async def test_07_lane_cfg_readback(dut):
    """LANE_CFG (lane register 0x18) keeps the bits of its table mask: the UART example
    writes 0x60 and reads it back."""
    addr = 0x1000 + regs_map.LANE["LANE_CFG"]["addr"]

    def fn(s, pins):
        pins.fault(0)
        s.load(MAIN + [(addr, [0x60])], verify=False)
        return s.peek(addr)[0]

    assert await run_host(dut, fn) == 0x60
