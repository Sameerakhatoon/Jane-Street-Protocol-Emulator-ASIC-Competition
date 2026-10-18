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
    "side-set and jump pin from LANE_CFG written with CSRW 0x18")
test_03_ldw_stw = lane_test(
    "test_03_ldw_stw", P.no_cfg(), P.body(P.DATA),
    "LDW and STW through the data window in the lane data slot")
test_04_timer_flag = lane_test(
    "test_04_timer_flag", P.TIMER_CFG, P.body([P.WAIT_TIMER, P.FRAME_RST]),
    "a WAIT 1, EV, 0 that ends on the TIMER flag clears it; TIMER_CTL at 0x15")
test_05_rule_chain = lane_test(
    "test_05_rule_chain", P.RULES_CFG, P.body(),
    "response rules from 0x40 to 0x48, rule 1 chained to rule 0")
test_06_illegal = lane_test(
    "test_06_illegal", P.no_cfg(), P.body([P.ILLEGAL]),
    "an illegal word halts the lane and raises interrupt bit 3")
test_07_wait_flags = lane_test(
    "test_07_wait_flags", P.no_cfg(), P.wait_pairs(),
    "TFLAG and AFLAG clear when the next WAIT is dispatched")
test_08_clock_ts = lane_test(
    "test_08_clock_ts", P.no_cfg(), P.body([P.CLOCK]),
    "CSRR of CLOCK (0x26) and TS (0x28)")
test_09_status = lane_test(
    "test_09_status", P.no_cfg(), P.body(),
    "STATUS in the register table layout, read by the host every cycle", status=True)
test_10_synced = lane_test(
    "test_10_synced", P.no_cfg(), P.body([P.WAIT_SYNCED]),
    "FLAG 6 (SYNCED) reads 1 while the DPLL is idle")
test_11_illegal_data = lane_test(
    "test_11_illegal_data", P.no_cfg(), P.body([P.ILLEGAL_DATA]),
    "RTL tse_seq starts the data window access of an illegal LDW or STW (indirect with a nonzero "
    "address): the request is not gated by tse_legal, so OSR loads the word (or STW writes it) "
    "although the lane halts with no effect", expect_fail=True)
