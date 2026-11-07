# SPDX-License-Identifier: Apache-2.0
"""Build the spec testbench with Verilator once, run a directory of tests, read the traces back."""
import pathlib
import shutil
import subprocess

from seqspec_ref import FIELDS

ROOT = pathlib.Path(__file__).resolve().parents[2]
BUILD = ROOT / "build" / "seqspec"
SOURCES = ["src/tse_isa_pkg.sv", "src/tse_regs_pkg.sv", "src/tse_seq_spec.sv", "verif/seqspec/tb_seq_spec.sv"]
COPIES = 8                              # testbench copy i runs lane i // 2 at fetch latency i % 2 + 1


def lane_lat(i):
    return i // 2, i % 2 + 1


def have_verilator():
    return shutil.which("verilator") is not None


def build():
    exe = BUILD / "obj" / "Vtb_seq_spec"
    srcs = [ROOT / s for s in SOURCES]
    if exe.exists() and all(exe.stat().st_mtime > s.stat().st_mtime for s in srcs):
        return exe
    (BUILD / "obj").mkdir(parents=True, exist_ok=True)
    subprocess.run(["verilator", "--binary", "-Wall", "-j", "2", "--top-module", "tb_seq_spec",
                    "--Mdir", str(BUILD / "obj"), *map(str, srcs)],
                   check=True, cwd=ROOT, capture_output=True, text=True)
    return exe


def run(d, tests, cycles):
    """Run tests 0 .. tests-1 in d; yield each test's traces[copy] = list of row dicts.

    Traces are read one test at a time (and their files deleted) to keep memory small.
    """
    exe = build()
    subprocess.run([str(exe), f"+dir={d}", f"+tests={tests}", f"+cycles={cycles}"],
                   check=True, capture_output=True, text=True)
    for n in range(tests):
        path = pathlib.Path(d) / f"t{n}.trace"
        rows = [[None] * cycles for _ in range(COPIES)]
        for line in path.read_text().split("\n"):
            if not line:
                continue
            v = line.split()
            k, i = int(v[0]), int(v[1])
            r = {f: int(s, 16) for f, s in zip(FIELDS, v[2:])}
            r["fword"] = r["fword"] if r["fetch"] else None
            if not r["cfg_we"]:
                r["cfg_addr"] = r["cfg_wdata"] = None
            rows[i][k] = r
        path.unlink()
        yield rows


def first_diff(got, want):
    """(cycle, field, got, want) of the first difference, or None."""
    for k, (g, w) in enumerate(zip(got, want)):
        for f in FIELDS:
            if g[f] != w[f]:
                return k, f, g[f], w[f]
    return None
