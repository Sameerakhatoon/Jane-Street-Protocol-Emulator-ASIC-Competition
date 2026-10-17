"""Lane lockstep tests: tse_lane against model.lane on Hypothesis programs.

expect_fail marks a known difference between the RTL and the ISA; the
docstring says which. When the RTL is fixed the test passes, cocotb reports
the unexpected pass, and the mark comes off.
"""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.task import bridge, resume
from hypothesis import HealthCheck, Phase, given, note, settings, strategies as st
from hypothesis.database import DirectoryBasedExampleDatabase

import lane_progs as P
from lane_lockstep import assemble, run_lane

NC = int(os.environ.get("LOCKSTEP_CYCLES", "120"))
EXAMPLES = int(os.environ.get("HYP_EXAMPLES", "100"))
IN_CI = os.environ.get("CI") == "true"


def lane_test(name, cfgs, bodies, doc, expect_fail=False, status=False, cycles=0):
    async def t(dut):
        cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
        db = None if IN_CI else DirectoryBasedExampleDatabase(f"sim_build/.hypothesis-{name}")
        phases = [Phase.explicit, Phase.reuse, Phase.generate] if expect_fail else list(Phase)

        @bridge
        def run():
            @settings(max_examples=EXAMPLES, deadline=None, derandomize=IN_CI, database=db,
                      suppress_health_check=list(HealthCheck), phases=phases)
            @given(cfgs, bodies, st.integers(0, 2), P.waves)
            def prop(cfg, body, lane, wave):
                prog = assemble(cfg, body)
                n = cycles or NC + 16 * len(cfg)          # about 16 cycles per preamble word
                report = resume(run_lane)(dut, prog, lane, P.expand(wave, n), n, status)
                if report:
                    note(report)
                assert report is None, report.splitlines()[0]
            prop()

        await run()
    t.__name__ = t.__qualname__ = name
    t.__doc__ = doc
    return cocotb.test(expect_fail=expect_fail)(t)


test_01_core = lane_test(
    "test_01_core", P.no_cfg(), P.body(),
    "straight-line code, mailboxes, runtime CSR reads and SET CMD frame resets through tse_lane")
test_02_lane_cfg = lane_test(
    "test_02_lane_cfg", P.LANE_CFG, P.body([P.JMP_PIN]),
    "RTL keeps LANE_CFG in configuration word 9 with its own bit layout; CSRW 0x18 (side-set, "
    "jump pin) does not reach it", expect_fail=True)
test_03_ldw_stw = lane_test(
    "test_03_ldw_stw", P.no_cfg(), P.body(P.DATA),
    "RTL has no LDW or STW: the words decode as CSRW and CSRR", expect_fail=True)
test_04_timer_flag = lane_test(
    "test_04_timer_flag", P.TIMER_CFG, P.body([P.WAIT_TIMER, P.FRAME_RST]),
    "RTL does not clear the TIMER flag when a WAIT 1, EV, 0 it satisfied ends (and TIMER_CTL is "
    "not at 0x15)", expect_fail=True)
test_05_rule_chain = lane_test(
    "test_05_rule_chain", P.RULES_CFG, P.body(),
    "RTL has no rule chaining (RULE1_CTL.chain), and the rule registers are not at 0x40 to 0x48",
    expect_fail=True)
test_06_illegal = lane_test(
    "test_06_illegal", P.no_cfg(), P.body([P.ILLEGAL]),
    "RTL does not halt on an illegal word, set STATUS.illegal or raise interrupt bit 3",
    expect_fail=True)
test_07_wait_flags = lane_test(
    "test_07_wait_flags", P.no_cfg(), P.wait_pairs(),
    "RTL clears TFLAG and AFLAG when a WAIT ends, not when it is dispatched", expect_fail=True)
test_08_clock_ts = lane_test(
    "test_08_clock_ts", P.no_cfg(), P.body([P.CLOCK]),
    "RTL CSRR of CLOCK (0x26) and TS (0x28) read 0", expect_fail=True)
test_09_status = lane_test(
    "test_09_status", P.no_cfg(), P.body(), "RTL STATUS has its own layout: no TFLAG at bit 8, "
    "running at 10, hit flags at 12:11 or PC at 31:24", expect_fail=True, status=True)
test_10_synced = lane_test(
    "test_10_synced", P.no_cfg(), P.body([P.WAIT_SYNCED]),
    "RTL ties FLAG 6 (SYNCED) to 0: an idle DPLL is not waiting for a hard sync, so it reads 1",
    expect_fail=True)
