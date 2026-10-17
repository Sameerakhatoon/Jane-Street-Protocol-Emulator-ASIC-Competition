"""Block-level lockstep: one RTL assist against its model, cycle by cycle.

Each cycle the inputs are applied after the clock edge, the RTL outputs are
read in ReadOnly and compared with what the model says is visible in that
cycle, then the model steps. Hypothesis draws the configuration and a
per-cycle input stream; the first difference is reported with the inputs
of the last cycles.
"""
import os

import cocotb
from cocotb.clock import Clock
from cocotb.task import bridge, resume
from cocotb.triggers import ReadOnly, RisingEdge
from hypothesis import HealthCheck, Phase, given, note, settings
from hypothesis.database import DirectoryBasedExampleDatabase

EXAMPLES = int(os.environ.get("HYP_EXAMPLES", "100"))
IN_CI = os.environ.get("CI") == "true"


async def run_block(dut, cfg, model, stream, outs, to_model, rtl_cfg=dict):
    """cfg: port -> value held for the run; stream: list of port -> value per cycle.
    Returns None or a report of the first difference."""
    dut.rst_n.value = 0
    for k, v in rtl_cfg(cfg).items():
        getattr(dut, k).value = v
    for k in stream[0]:
        getattr(dut, k).value = 0
    await RisingEdge(dut.clk)
    dut.rst_n.value = 1
    hist = []
    for c, iv in enumerate(stream):
        for k, v in iv.items():
            getattr(dut, k).value = v
        await ReadOnly()
        got = {k: int(getattr(dut, k).value) for k in outs}
        want = model.step(to_model(iv))
        hist.append(iv)
        await RisingEdge(dut.clk)
        bad = [k for k in outs if got[k] != want[k]]
        if bad:
            recent = "\n".join(f"  {c - len(hist[-6:]) + 1 + j}: {h}" for j, h in enumerate(hist[-6:]))
            return (f"cycle {c}: {', '.join(f'{k} model {want[k]:#x} RTL {got[k]:#x}' for k in bad)}\n"
                    f"config {cfg}\nlast inputs:\n{recent}")
    return None


def block_test(name, cfgs, streams, build, outs, to_model, expect_fail=False, doc="", rtl_cfg=dict):
    """A cocotb test: Hypothesis draws (cfg, stream); build(cfg) makes the model and
    rtl_cfg(cfg) the port values (by default cfg itself)."""
    async def t(dut):
        cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
        db = None if IN_CI else DirectoryBasedExampleDatabase(f"sim_build/.hypothesis-{name}")
        # a known difference needs one counterexample, not a minimal one
        phases = [Phase.explicit, Phase.reuse, Phase.generate] if expect_fail else list(Phase)

        @bridge
        def run():
            @settings(max_examples=EXAMPLES, deadline=None, derandomize=IN_CI, database=db,
                      suppress_health_check=list(HealthCheck), phases=phases)
            @given(cfgs, streams)
            def prop(cfg, stream):
                report = resume(run_block)(dut, cfg, build(cfg), stream, outs, to_model, rtl_cfg)
                if report:
                    note(report)
                assert report is None, report.splitlines()[0]
            prop()

        await run()
    t.__name__ = t.__qualname__ = name
    t.__doc__ = doc
    return cocotb.test(expect_fail=expect_fail)(t)
