# SPDX-License-Identifier: Apache-2.0
"""The command line against the model, with a state file, and over the pin pipe."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(HERE)
CLI = os.path.join(HOST, "tse_cli.py")
UART = os.path.join(HOST, "..", "tools", "tseasm", "tests", "uart_tx.s")


def cli(*args, ok=True):
    r = subprocess.run([sys.executable, CLI] + list(args), capture_output=True, text=True)
    assert (r.returncode == 0) == ok, r.stdout + r.stderr
    return r.stdout


# 1: image build from an assembler source, then show
def test_01_image_build_show(tmp_path):
    out = str(tmp_path / "uart.tsei")
    text = cli("image", "build", "-o", out, "--lane", "0=" + UART, "--chip", "PAD_OUT.0=0x1C0")
    assert "sram window 0" in text and "lane 0 registers" in text and "xbar" in text
    assert "clock  60000000 Hz" in cli("image", "show", out)


# 2: load, peek, poke and status across separate runs through the state file
def test_02_model_state(tmp_path):
    img, state = str(tmp_path / "u.tsei"), str(tmp_path / "chip.json")
    cli("image", "build", "-o", img, "--lane", "1=" + UART)
    assert "loaded u: " in cli("--state", state, "load", img)
    assert "loaded=1" in cli("--state", state, "status") and "mode load" in cli("--state", state, "status")
    assert cli("--state", state, "peek", "0x1119") == "0x1119: 0x00000000\n"     # LANE_PROG window 0
    cli("--state", state, "poke", "0x0400", "0xBEEF")
    assert cli("--state", state, "peek", "0x0400", "2").startswith("0x0400: 0x0000BEEF\n")
    text = cli("--state", state, "load", img, "--run", "2", "+", "status", "+", "peek", "0x4001")
    assert "running=2" in text and "mode run" in text and "0x4001: 0x00000002" in text


# 3: the same commands through the pin pipe, against a model in another process
def test_03_pipe_target(tmp_path):
    img = str(tmp_path / "u.tsei")
    cli("image", "build", "-o", img, "--lane", "0=" + UART)
    target = "pipe:%s -m tse_host.pinpipe 3" % sys.executable
    text = cli("--target", target, "load", img, "+", "peek", "0", "2", "+", "status")
    assert "0x0000: 0x41FD4011\n0x0001: 0x4207C300" in text and "loaded=1" in text


# 4: errors come back as a message and a non-zero exit
def test_04_errors(tmp_path):
    bad = tmp_path / "bad.tsei"
    bad.write_bytes(b"TSEI\x01" + b"\x00" * 12)
    assert cli("load", str(bad), ok=False) == ""
