# SPDX-License-Identifier: Apache-2.0
"""10: judge the simulation harness's pins-only VCD of a UART round trip.

Uses build/echo/tse_sim and tests/pty_client.py under sim/ (TSE_SIM_DIR
overrides the folder). Skips when the harness has not been built.
"""
import os
import subprocess
import sys

import pytest

from verif.judges import uart
from verif.judges.vcd import read_vcd

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
SIM_DIR = os.environ.get("TSE_SIM_DIR", os.path.join(ROOT, "sim"))
SIM = os.path.join(SIM_DIR, "build", "echo", "tse_sim")
CLIENT = os.path.join(SIM_DIR, "tests", "pty_client.py")


def test_harness_uart_vcd(tmp_path):
    if not (os.access(SIM, os.X_OK) and os.path.exists(CLIENT)):
        pytest.skip("simulation harness not built")
    vcd = str(tmp_path / "p.vcd")
    p = subprocess.run([sys.executable, CLIENT, "61", "41", "--", SIM, "--uart", "--vcd", vcd],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stdout + p.stderr
    tr = read_vcd(vcd, {"rx": "ui_in0", "tx": "uo_out0"})
    res = uart.decode(tr, baud=115200, baud_tol=0.5)
    assert res.keys() == ["rx 61", "tx 41"]
    assert res.violations == []
