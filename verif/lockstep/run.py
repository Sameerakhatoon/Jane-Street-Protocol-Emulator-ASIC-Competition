"""Build seq_harness with the RTL in $RTL_DIR and run test_lockstep with cocotb 2.1.

SIM=icarus|verilator. TESTCASE=name runs one test. Exit status 1 if any test
failed unexpectedly (expect_fail tests that fail count as passes).
"""
import os
import sys
from pathlib import Path

from cocotb_tools.runner import get_runner

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
rtl = Path(os.environ.get("RTL_DIR", ROOT / "src"))
sim = os.environ.get("SIM", "icarus")
args = ["-g2012"] if sim == "icarus" else ["-Wno-fatal", "--public-flat-rw"]
build = HERE / "sim_build" / sim
sys.path[:0] = [str(ROOT), str(HERE)]      # the runner passes sys.path on as PYTHONPATH

runner = get_runner(sim)
runner.build(sources=[rtl / "tse_seq.sv", HERE / "seq_harness.sv"], hdl_toplevel="seq_harness",
             build_args=args, build_dir=build, timescale=("1ns", "1ps"))
results = Path(runner.test(hdl_toplevel="seq_harness", test_module="test_lockstep",
                           build_dir=build, test_dir=HERE,
                           testcase=os.environ.get("TESTCASE") or None))
sys.exit(1 if not results.exists() or "<failure" in results.read_text() else 0)
