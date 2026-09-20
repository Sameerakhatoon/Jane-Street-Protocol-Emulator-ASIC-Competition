"""Build tse_loader.sv with Icarus and run the cocotb tests in proto_loader.py.

    python3 host/tests/rtl/run_rtl.py        (from the repository root)
Exit status 0 when every test passes or fails exactly as marked.
"""
import os
import sys

from cocotb_tools.runner import get_results, get_runner

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(os.path.dirname(HERE))
BUILD = os.path.join(os.path.dirname(HOST), "build", "host_rtl")


def main():
    os.environ["PYTHONPATH"] = os.pathsep.join([HERE, HOST, os.environ.get("PYTHONPATH", "")])
    sys.path[:0] = [HERE, HOST]
    r = get_runner("icarus")
    r.build(sources=[os.path.join(HERE, "tse_loader.sv")], hdl_toplevel="tse_loader",
            build_dir=BUILD, build_args=["-g2012"], timescale=("1ps", "1ps"), always=True)
    results = r.test(hdl_toplevel="tse_loader", test_module="proto_loader", build_dir=BUILD,
                     test_dir=BUILD)
    total, failed = get_results(results)
    print("rtl: %d tests, %d failed" % (total, failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
