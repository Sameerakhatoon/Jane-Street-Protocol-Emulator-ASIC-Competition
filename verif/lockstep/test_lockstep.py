"""Lockstep tests: tse_seq against the reference model on Hypothesis programs.

expect_fail marks a known difference between the RTL and the ISA; the
docstring says which. When the RTL is fixed the test passes, cocotb reports
the unexpected pass, and the mark comes off.
"""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.task import bridge, resume
from hypothesis import HealthCheck, given, note, settings
from hypothesis.database import DirectoryBasedExampleDatabase

from lockstep import run_lockstep
from model.isa import encode as E
from progs import programs, setups, waves

NC = int(os.environ.get("LOCKSTEP_CYCLES", "120"))
EXAMPLES = int(os.environ.get("HYP_EXAMPLES", "100"))
IN_CI = os.environ.get("CI") == "true"
UART = [0x4011, 0x41FD, 0xC300, 0x4207, 0x4810, 0xE806, 0x8806, 0x0A06, 0x4011, 0x0002]
SQUARE = [E("SET", dst=1, imm=0xF3), E("DLY"), E("DLY", ss=1, d=4), E("DLY", d=2),
          E("JMP", cond=0, target=2), E("DLY")]


def setup(lane, ss_base=0, side_en=1):
    return dict(lane=lane, side_en=side_en, ss_base=ss_base, jmp_pin=0, alt_pol=0, alt=(0, 0))


async def directed(dut, prog, lanes, wave, ncycles, **kw):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    for lane in lanes:
        report = await run_lockstep(dut, prog, setup(lane, **kw), wave, ncycles)
        assert report is None, report


async def search(dut, name, profile, setup_strategy):
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    db = None if IN_CI else DirectoryBasedExampleDatabase(f"sim_build/.hypothesis-{name}")

    @bridge
    def run():
        @settings(max_examples=EXAMPLES, deadline=None, derandomize=IN_CI, database=db,
                  suppress_health_check=list(HealthCheck))
        @given(programs(profile), setup_strategy, waves)
        def prop(prog, setup_, wave):
            report = resume(run_lockstep)(dut, prog, setup_, wave, NC)
            if report:
                note(report)
            assert report is None, report.splitlines()[0]
        prop()

    await run()


@cocotb.test()
async def test_01_uart_directed(dut):
    """UART TX at 8 cycles per bit on both lanes, the host feeding two words."""
    wave = [(2, 1 << 16), (60, 0), (2, 1 << 16), (200, 0)]
    await directed(dut, UART + [E("JMP", cond=0, target=10)] * 6, range(2), wave, 240, ss_base=2)


@cocotb.test()
async def test_02_square_wave_two_word_loop(dut):
    """A loop over two words runs fetch-free: period 9, not a multiple of 4."""
    await directed(dut, SQUARE, [1], [(1, 0)], 80)


@cocotb.test()
async def test_03_straight_line(dut):
    """No word fetched twice: effects, delays, mailboxes, CSR costs, first-use fetch stalls."""
    await search(dut, "straight", "straight", setups())


def finding(name, profile, doc, ss_bases=(0, 1, 2), xfail=True):
    async def t(dut):
        await search(dut, name, profile, setups(ss_bases))
    t.__name__ = t.__qualname__ = name
    t.__doc__ = doc
    return cocotb.test(expect_fail=xfail)(t)


test_04_branches = finding(
    "test_04_branches", "branch",
    "A fill replaces the least recently used word; the word of the current instruction stays", xfail=False)
test_05_wait = finding(
    "test_05_wait", "wait",
    "WAIT and blocking PUSH or PULL fetch their successor from dispatch on", xfail=False)
test_06_side_set_base3 = finding(
    "test_06_side_set_base3", "straight",
    "Side-set from pin 3 drives pins 3 and 0 (wrap modulo 4)", ss_bases=(3,), xfail=False)
test_07_set_mask = finding(
    "test_07_set_mask", "set_mask",
    "SET PINS and SET PINDIRS write imm[3:0] under the mask imm[7:4]", xfail=False)
test_08_jmp_guard = finding(
    "test_08_jmp_guard", "jmp_guard",
    "JMP condition 7 is GUARD (C >= GT[gsel])", xfail=False)
test_09_wait_edge_chip_guard = finding(
    "test_09_wait_edge_chip_guard", "wait_edge_chip_guard",
    "WAIT has the pin-changed, CHIP and GUARD sources", xfail=False)
test_10_mov_clock = finding(
    "test_10_mov_clock", "mov_clock",
    "MOV source 7 reads the clock C", xfail=False)
test_11_tmr_sig_emit = finding(
    "test_11_tmr_sig_emit", "tmr_sig_emit",
    "TMR, SIG and EMIT decode with their own durations, DLY has a 9-bit count", xfail=False)
test_12_csr_classes = finding(
    "test_12_csr_classes", "csr_classes",
    "CSRW costs 3 for every class-L address, the rule registers included, and 1 otherwise",
    xfail=False)
