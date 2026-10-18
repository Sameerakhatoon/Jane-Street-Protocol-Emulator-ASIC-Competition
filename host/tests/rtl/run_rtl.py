"""Build the whole chip with Icarus and run the cocotb tests in loader_rtl.py and
session_rtl.py at 3 lanes, then session_rtl.py again on the 4-lane build.

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
    total = failed = 0
    for lanes, modules in ((3, ["loader_rtl", "session_rtl"]), (4, ["session_rtl"])):
        build = "%s_L%d" % (BUILD, lanes)
        r = get_runner("icarus")
        r.build(sources=sources(), hdl_toplevel="tse_chip", build_dir=build,
                parameters={"LANES": lanes}, build_args=["-g2012", "-DSIM"],
                timescale=("1ps", "1ps"), always=True)
        results = r.test(hdl_toplevel="tse_chip", test_module=modules, build_dir=build,
                         test_dir=build, extra_env={"LANES": str(lanes)})
        t, f = get_results(results)
        print("rtl %d lanes: %d tests, %d failed" % (lanes, t, f))
        total, failed = total + t, failed + f
    print("rtl: %d tests, %d failed" % (total, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
