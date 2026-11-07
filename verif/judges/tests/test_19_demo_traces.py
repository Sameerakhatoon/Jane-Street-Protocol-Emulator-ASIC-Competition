# SPDX-License-Identifier: Apache-2.0
"""19: the stand-in demos recorded with real OpenOCD and Linux ping, judged.

Runs demos/record.sh into a temporary folder and the stand_in.txt manifest
against it. Skips when the harness is not built (sim/build/echo/tse_sim);
a demo whose tool is missing (openocd, user namespaces) is skipped alone.
"""
import io
import os
import subprocess

import pytest

from verif.judges.run_judges import main

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
DEMOS = os.path.join(ROOT, "verif", "judges", "demos")
SIM = os.path.join(ROOT, "sim", "build", "echo", "tse_sim")


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    if not os.access(SIM, os.X_OK):
        pytest.skip("simulation harness not built")
    out = tmp_path_factory.mktemp("demos")
    p = subprocess.run(["bash", os.path.join(DEMOS, "record.sh"), str(out)],
                       capture_output=True, text=True, timeout=300)
    assert p.returncode == 0, p.stdout + p.stderr
    return out, p.stdout


def judge(recorded, tmp_path, names):
    out, _ = recorded
    lines = []
    with open(os.path.join(DEMOS, "stand_in.txt")) as f:
        for ln in f:
            w = ln.split()
            if w and w[0] in names:
                vcd = os.path.join(str(out), os.path.basename(w[2]))
                if not os.path.exists(vcd):
                    pytest.skip("%s was not recorded" % w[0])
                exp = w[3] if w[3] == "-" else os.path.join(DEMOS, w[3])
                lines.append(" ".join([w[0], w[1], vcd, exp] + w[4:]))
    m = tmp_path / "m.txt"
    m.write_text("\n".join(lines) + "\n")
    buf = io.StringIO()
    rc = main([str(m), "--verbose"], out=buf)
    return rc, buf.getvalue()


def test_openocd_swd_reads_dpidr_with_no_violations(recorded, tmp_path):
    rc, text = judge(recorded, tmp_path, ["openocd_swd"])
    assert rc == 0, text
    assert "DP R 0x0 OK 0BC11477" in text and "DP R 0x4 OK F0000001" in text


def test_openocd_jtag_idcode(recorded, tmp_path):
    rc, text = judge(recorded, tmp_path, ["openocd_jtag"])
    assert rc == 0, text
    assert "IDCODE 15E00001" in text


def test_tap_ping_both_directions(recorded, tmp_path):
    rc, text = judge(recorded, tmp_path, ["tap_ping_tx", "tap_ping_rx"])
    assert rc == 0, text
    assert "ERROR" not in text and " 0800 " in text and " 0806 " in text
