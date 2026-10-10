"""cocotb: the memories of the whole chip through its pins.

The self-test on the macro's test port with each fault class injected in the
SRAM model, the switch to the test port with idle cycles around it, a program
running from the fallback store with the SRAM dead, the fallback store
against the SRAM with the same program (same slot, so the same timing one
cycle apart), and host access to the data window and the fallback store.
"""
import os

import cocotb
from cocotb.triggers import FallingEdge

from tse_host import frames as F
from tools.tseasm import regs
from chip_pins import Pins

LANES = int(os.environ.get("LANES", "3"))
LOADED, BIST_DONE, BIST_PASS, HOST_ERR = 0x01, 0x02, 0x04, 0x08
FB = 0x0800
DS_WORDS = 16

# SET PINDIRS 0x11; loop: SET PINS 0x11; SET X 0; SET PINS 0x10; JMP 1 (pin 0 high 2,
# low 2 instructions; the loop spans three words, so every iteration refetches)
PROG = [0x40114111, 0x40104200, 0x00010001]


def macro(dut):
    return dut.u_top.u_pmem.u_sram.u_macro


async def set_fault(dut, kind):
    await FallingEdge(dut.clk)
    macro(dut).fault_kind.value = kind


async def run_bist(p):
    await p.exchange(F.begin() + F.bist())
    await p.tick(12000)
    return await p.stat(), (await p.read(regs.host_addr("BIST")))[0]


def lane_prog(window=0, fallback=0):
    return window << 8 | fallback << 10


async def load_and_run(p, blocks, run_mask):
    body, crc = F.load_body(blocks)
    await p.exchange(body + F.commit(crc))
    assert await p.stat() & LOADED
    await p.leave_load()
    await p.spi_write(regs.host_addr("RUN"), run_mask)


async def sample(p, n):
    out = []
    for _ in range(n):
        await p.tick(1)
        out.append(p.uo_out)
    return out


def edges(bits):
    return [i for i in range(1, len(bits)) if bits[i] != bits[i - 1]]


@cocotb.test()
async def test_01_bist_fault_classes(dut):
    """A clean SRAM passes; a stuck-at-1, stuck-at-0, transition, coupling and
    address-alias fault and a dead macro each fail, with the victim word for the cell
    faults; no access ever happens next to a change of the test-port select."""
    p = Pins(dut)
    await p.start()
    await set_fault(dut, 0)
    await p.enter_load()
    kinds = range(7) if LANES == 3 else (0, 1, 6)
    for kind in kinds:
        await set_fault(dut, kind)
        st, res = await run_bist(p)
        assert st & BIST_DONE, "kind %d: not done" % kind
        if kind == 0:
            assert st & BIST_PASS and res == 0
        else:
            assert not st & BIST_PASS and res >> 13 & 1, "kind %d: fault missed (BIST %04x)" % (kind, res)
            if kind <= 3:
                assert res & 0x1FF == 77, "kind %d: word %d" % (kind, res & 0x1FF)
    await set_fault(dut, 0)
    assert int(macro(dut).guard_errors.value) == 0


@cocotb.test()
async def test_02_fallback_runs_with_dead_sram(dut):
    """With the macro dead the self-test fails, and a lane fetching from the fallback
    store still runs its program: a steady square wave on uo_out[0]."""
    p = Pins(dut)
    await p.start()
    await set_fault(dut, 0)
    await p.enter_load()
    await set_fault(dut, 6)
    st, res = await run_bist(p)
    assert st & BIST_DONE and not st & BIST_PASS
    blocks = [(FB, PROG),
              (regs.host_addr("LANE_PROG", lane=0), [lane_prog(fallback=1)]),
              (regs.host_addr("PAD_OUT", index=0), [0x1C0])]
    await load_and_run(p, blocks, 1)
    bits = [v & 1 for v in await sample(p, 200)]
    e = edges(bits)
    assert len(e) >= 10, "no square wave: %s" % bits
    gaps = [b - a for a, b in zip(e, e[1:])][2:]
    assert len(set(gaps[0::2])) == 1 and len(set(gaps[1::2])) == 1, gaps
    assert sum(gaps[:2]) % 4 == 0
    await set_fault(dut, 0)


@cocotb.test()
async def test_03_fallback_same_timing(dut):
    """Lane 0 runs the program from SRAM window 0, lane 1 the same program from the
    fallback store: lane 1's pin is lane 0's pin one cycle later, every cycle."""
    p = Pins(dut)
    await p.start()
    await set_fault(dut, 0)
    await p.enter_load()
    await p.exchange(F.begin())
    blocks = [(0x0000, PROG), (FB, PROG),
              (regs.host_addr("LANE_PROG", lane=0), [lane_prog(0)]),
              (regs.host_addr("LANE_PROG", lane=1), [lane_prog(1, 1)]),
              (regs.host_addr("PAD_OUT", index=0), [0x1C0, 0x1C4])]
    await load_and_run(p, blocks, 3)
    v = await sample(p, 300)
    a = [x & 1 for x in v]
    b = [x >> 1 & 1 for x in v]
    assert len(edges(a)) >= 20
    assert a[:-1] == b[1:], "fallback lane differs from the SRAM lane"


@cocotb.test()
async def test_04_host_access(dut):
    """Data window and fallback store from the host: load-mode writes read back over
    the loader and SPI; while lanes run the data window stays writable (window 3 of
    the SRAM with 3 lanes, the latch store with 4, where words past the store read
    0); a fallback store write is refused while a lane runs from it."""
    p = Pins(dut)
    await p.start()
    await set_fault(dut, 0)
    await p.enter_load()
    await p.exchange(F.begin())
    data = [0x11110000 + i for i in range(8)]
    blocks = [(0x0000, PROG), (FB, PROG), (0x0400, data),
              (regs.host_addr("LANE_PROG", lane=0), [lane_prog(0, 1)])]
    body, crc = F.load_body(blocks)
    await p.exchange(body + F.commit(crc))
    assert await p.read(FB, 3) == PROG
    assert await p.read(0x0400, 8) == data
    if LANES < 4:
        assert await p.read(0x0180, 8) == data
    await p.leave_load()
    await p.spi_write(regs.host_addr("RUN"), 1)
    await p.tick(40)
    await p.spi_write(0x0403, 0xCAFEF00D)
    assert (await p.spi_read(0x0400, 4)) == data[:3] + [0xCAFEF00D]
    if LANES == 4:
        await p.spi_write(0x0400 + DS_WORDS, 0x12345678)
        assert (await p.spi_read(0x0400 + DS_WORDS))[0] == 0
    st = (await p.spi_read(regs.host_addr("STATUS")))[0]
    assert not st & HOST_ERR
    await p.spi_write(FB + 1, 0)
    assert (await p.spi_read(FB + 1))[0] == PROG[1]
    st = (await p.spi_read(regs.host_addr("STATUS")))[0]
    assert st & HOST_ERR
