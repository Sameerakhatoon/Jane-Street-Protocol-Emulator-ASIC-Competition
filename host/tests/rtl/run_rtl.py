"""Build the whole chip with Icarus and run the cocotb tests in loader_rtl.py and
session_rtl.py, with the lane count of the host model.

    python3 host/tests/rtl/run_rtl.py        (from the repository root)
The source list is the full-chip one from info_chip.yaml, with the SRAM model
instead of the black box; the pin driver comes from verif/chip.
Exit status 0 when every test passes.
"""
import os
import re
import sys

from cocotb_tools.runner import get_results, get_runner

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(os.path.dirname(HERE))
ROOT = os.path.dirname(HOST)
BUILD = os.path.join(ROOT, "build", "host_rtl")
PINS = os.path.join(ROOT, "verif", "chip")


def sources():
    text = open(os.path.join(ROOT, "info_chip.yaml")).read()
    block = text.split("source_files:")[1].split("\n\n")[0]
    names = re.findall(r'^\s*-\s*"([^"]+)"', block, re.M)
    src = [os.path.join(ROOT, "src", n) for n in names if n != "tse_sram_bb.v"]
    return src + [os.path.join(ROOT, "src", "tse_sram_model.v")]


def main():
    os.environ["PYTHONPATH"] = os.pathsep.join([HERE, HOST, PINS, os.environ.get("PYTHONPATH", "")])
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path[:0] = [HERE, HOST, PINS]
    from tse_host.model import LANES
    r = get_runner("icarus")
    r.build(sources=sources(), hdl_toplevel="tse_chip", build_dir=BUILD,
            parameters={"LANES": LANES}, build_args=["-g2012", "-DSIM"],
            timescale=("1ps", "1ps"), always=True)
    results = r.test(hdl_toplevel="tse_chip", test_module=["loader_rtl", "session_rtl"],
                     build_dir=BUILD, test_dir=BUILD)
    total, failed = get_results(results)
    print("rtl %d lanes: %d tests, %d failed" % (LANES, total, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
