"""Build each assist block from $RTL_DIR and run its lockstep tests with cocotb 2.1.

SIM=icarus|verilator. BLOCK=name runs one block. Exit status 1 if any test
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
sys.path[:0] = [str(ROOT), str(HERE)]

# block: (toplevel, sources, parameters)
BLOCKS = {
    "crc":     ("tse_crc", ["tse_crc.sv"], {}),
    "stuffer": ("tse_stuffer", ["tse_stuffer.sv"], {}),
    "codec":   ("tse_codec", ["tse_codec.sv"], {}),
    "dpll":    ("tse_dpll", ["tse_dpll.sv"], {}),
    "timer":   ("tse_timer", ["tse_nco.sv", "tse_timer.sv"], {}),
    "rules":   ("tse_resp_rule", ["tse_resp_rule.sv"], {"K": 2, "PW": 16}),
}

failed = []
for name, (top, srcs, params) in BLOCKS.items():
    if os.environ.get("BLOCK") not in (None, "", name):
        continue
    build = HERE / "sim_build" / sim / name
    args = ["-g2012"] if sim == "icarus" else ["-Wno-fatal", "--public-flat-rw"]
    runner = get_runner(sim)
    runner.build(sources=[rtl / s for s in srcs], hdl_toplevel=top, parameters=params,
                 build_args=args, build_dir=build, timescale=("1ns", "1ps"))
    res = Path(runner.test(hdl_toplevel=top, test_module=f"test_{name}", build_dir=build,
                           test_dir=HERE, testcase=os.environ.get("TESTCASE") or None))
    if not res.exists() or "<failure" in res.read_text():
        failed.append(name)
print("block lockstep:", "failed " + ", ".join(failed) if failed else "all blocks as expected")
sys.exit(1 if failed else 0)
