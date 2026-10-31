#!/usr/bin/env python3
"""Stage and run one MCY project: build/formal/mcy_<block>/.

The project gets formal/mcy/<block>/*, the shared test step, the block's
proof harnesses and the RTL file from RTL_DIR, then mcy init, run, status.

usage: run_mcy.py <block> [--rtl DIR] [--build DIR] [--jobs N]
"""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
FORMAL = HERE.parent
ROOT = FORMAL.parent

# block: (RTL file, proof harnesses under formal/)
BLOCKS = {
    "crc": ("tse_crc.sv", ["crc/crc_props.sv", "crc/crc_full.sv"]),
    "stuffer": ("tse_stuffer.sv", ["stuffer/stuff_props.sv"]),
    "codec": ("tse_codec.sv", ["codec/codec_props.sv"]),
    "rule": ("tse_resp_rule.sv", ["rule/rule_props.sv"]),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("block", choices=sorted(BLOCKS))
    ap.add_argument("--rtl", default=str(ROOT / "src"))
    ap.add_argument("--build", default=str(ROOT / "build" / "formal"))
    ap.add_argument("--jobs", default="2")
    a = ap.parse_args()
    rtl_file, harnesses = BLOCKS[a.block]
    out = Path(a.build).resolve() / f"mcy_{a.block}"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HERE / a.block, out)
    shutil.copy(HERE / "common" / "run_test.sh", out)
    shutil.copy(Path(a.rtl) / rtl_file, out)
    for h in harnesses:
        shutil.copy(FORMAL / h, out)
    for cmd in (["mcy", "init"], ["mcy", "run", f"-j{a.jobs}"], ["mcy", "status"]):
        if subprocess.run(cmd, cwd=out).returncode:
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
