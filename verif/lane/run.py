"""Build lane_harness with the RTL in $RTL_DIR and run test_lane with cocotb 2.1.

SIM=icarus|verilator. TESTCASE=name runs one test.
"""
import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
rtl = Path(os.environ.get("RTL_DIR", ROOT / "src"))
sim = os.environ.get("SIM", "icarus")
build = HERE / "sim_build" / sim
sys.path[:0] = [str(ROOT), str(HERE), str(HERE.parent / "lockstep")]

srcs = sorted(rtl.glob("*_pkg.sv")) + [rtl / f for f in ("tse_cfg_bank.sv", "tse_crc.sv", "tse_stuffer.sv",
        "tse_codec.sv", "tse_dpll.sv", "tse_resp_rule.sv", "tse_nco.sv", "tse_timer.sv", "tse_seq.sv",
        "tse_lane.sv")] + [HERE / "lane_harness.sv"]
args = ["-g2012"] if sim == "icarus" else ["-Wno-fatal", "--public-flat-rw"]
runner = get_runner(sim)
runner.build(sources=srcs, hdl_toplevel="lane_harness", build_args=args, build_dir=build,
             timescale=("1ns", "1ps"))
results = Path(runner.test(hdl_toplevel="lane_harness", test_module="test_lane", build_dir=build,
                           test_dir=HERE, testcase=os.environ.get("TESTCASE") or None))
sys.exit(1 if not results.exists() or "<failure" in results.read_text() else 0)
