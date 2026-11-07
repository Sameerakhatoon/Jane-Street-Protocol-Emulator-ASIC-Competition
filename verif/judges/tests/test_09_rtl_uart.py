# SPDX-License-Identifier: Apache-2.0
"""09: judge a real simulator VCD: the chip's UART transmitter under Icarus.

Skips when iverilog or the RTL is missing. RTL_DIR overrides the source folder.
"""
import os
import shutil
import subprocess

import pytest

from verif.judges import uart
from verif.judges.vcd import read_vcd

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
RTL = os.environ.get("RTL_DIR", os.path.join(ROOT, "src"))


def test_rtl_uart_tx_under_icarus(tmp_path):
    srcs = [os.path.join(RTL, f) for f in ("tse_nco.sv", "tse_uart_tx.sv")]
    if not shutil.which("iverilog") or not all(os.path.exists(s) for s in srcs):
        pytest.skip("iverilog or the UART RTL is not available")
    vvp = str(tmp_path / "tb.vvp")
    vcd = str(tmp_path / "tx.vcd")
    subprocess.run(["iverilog", "-g2012", "-o", vvp, os.path.join(HERE, "rtl", "uart_tx_tb.sv")] + srcs,
                   check=True, capture_output=True)
    subprocess.run(["vvp", "-n", vvp, "+vcd=" + vcd], check=True, capture_output=True, timeout=120)
    res = uart.decode(read_vcd(vcd, {"rx": "txd"}), baud=115200, baud_tol=0.5, edge_tol=0.05)
    assert res.keys() == ["41", "00", "FF", "55", "0A"]
    assert res.violations == []
