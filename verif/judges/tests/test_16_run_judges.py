"""16: the manifest runner, with every new protocol through the command line."""
import io
import os
import subprocess
import sys

from verif.judges.run_judges import main
from verif.judges.tests.jwave import (JtagDev, Wave, eth_frame_bits, jtag_reset, jtag_scan, jtag_wave,
                                      manchester_wave, uart_tx, usb_line, usb_packet_bits, usb_wave)

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def traces(d):
    w = Wave()
    w.add("uo_out0", 1)
    uart_tx(w, "uo_out0", 10e-6, 0x41, 115200)
    w.write(str(d / "uart.vcd"), tail=100e-6)

    w = Wave()
    for n, v in (("tck", 0), ("tms", 1), ("tdi", 0), ("tdo", "z")):
        w.add(n, v)
    jtag_wave(w, jtag_reset() + jtag_scan((1 << 64) - 1, 64), [JtagDev()])
    w.write(str(d / "jtag.vcd"), tail=1e-6)

    w = Wave()
    w.add("dp", 0)
    w.add("dm", 1)
    t = usb_wave(w, 5e-6, usb_line(usb_packet_bits("IN", addr=2, endp=1))) + 3e-6
    usb_wave(w, t, usb_line(usb_packet_bits("NAK")))
    w.write(str(d / "usb.vcd"), tail=5e-6)

    w = Wave()
    w.add("line", 0)
    manchester_wave(w, "line", 10e-6, [1, 0, 1, 1, 0, 0, 1, 0], 1e6, "thomas")
    w.write(str(d / "man.vcd"), tail=10e-6)

    w = Wave()
    w.add("td", 0)
    manchester_wave(w, "td", 2e-6, eth_frame_bits(b"\xff" * 6, bytes(range(6)), 0x88B5, b"hi"), 10e6)
    w.write(str(d / "eth.vcd"), tail=2e-6)

    (d / "uart.exp").write_text("# one byte\n41\n")
    (d / "jtag.exp").write_text("RESET\nIDCODE 4BA00477\n")
    (d / "usb.exp").write_text("IN 2.1\nNAK\n")
    (d / "usb_wrong.exp").write_text("IN 2.1\nACK\n")


GOOD = """# name  protocol  vcd  expect  options
hello   uart        uart.vcd  uart.exp  --map rx=uo_out0 --baud 115200
chain   jtag        jtag.vcd  jtag.exp  --map tck=tck --map tms=tms --map tdi=tdi --map tdo=tdo --tdo-z
token   usb         usb.vcd   usb.exp   --map dp=dp --map dm=dm
thomas  manchester  man.vcd   -         --map line=line --bitrate 1e6 --convention thomas
frame   eth10       eth.vcd   -         --map td=td
"""


def call(d, text, *extra):
    (d / "m.txt").write_text(text)
    out = io.StringIO()
    rc = main([str(d / "m.txt")] + list(extra), out)
    return rc, out.getvalue().splitlines()


def test_all_cases_pass(tmp_path):
    traces(tmp_path)
    rc, lines = call(tmp_path, GOOD)
    assert rc == 0, "\n".join(lines)
    assert lines[0].split() == ["case", "protocol", "frames", "violations", "result"]
    rows = {ln.split()[0]: ln.split()[1:] for ln in lines[1:6]}
    assert rows["hello"] == ["uart", "1", "0", "PASS"]
    assert rows["chain"] == ["jtag", "2", "0", "PASS"]
    assert rows["token"] == ["usb", "2", "0", "PASS"]
    assert rows["thomas"] == ["manchester", "1", "0", "PASS"]
    assert rows["frame"] == ["eth10", "1", "0", "PASS"]
    assert lines[-1] == "PASS: 5 cases, 5 passed, 0 failed, 0 skipped"


def test_skip_counts_as_failure_unless_allowed(tmp_path):
    traces(tmp_path)
    rc, lines = call(tmp_path, GOOD + "later uart nothere.vcd - --map rx=uo_out0 --baud 115200\n")
    assert rc == 1
    assert any("SKIP (counts as failure)" in ln for ln in lines)
    assert lines[-1] == "FAIL: 6 cases, 5 passed, 1 failed, 1 skipped"
    rc, lines = call(tmp_path, GOOD + "later uart nothere.vcd - --map rx=uo_out0 --baud 115200 --allow-skip\n")
    assert rc == 0
    assert lines[-1] == "PASS: 6 cases, 5 passed, 0 failed, 1 skipped"


def test_expect_mismatch_and_bad_options_fail_with_output(tmp_path):
    traces(tmp_path)
    rc, lines = call(tmp_path, "wrong usb usb.vcd usb_wrong.exp --map dp=dp --map dm=dm\n"
                               "bad uart uart.vcd - --map rx=uo_out0\n")
    assert rc == 1
    assert any(ln.split()[:2] == ["wrong", "usb"] and ln.endswith("FAIL") for ln in lines)
    assert any(ln.split()[:2] == ["bad", "uart"] and ln.endswith("ERROR") for ln in lines)
    assert "== wrong (line 1)" in lines and any("+ACK" in ln or "-ACK" in ln for ln in lines)


def test_bad_manifest_exits_2(tmp_path):
    (tmp_path / "m.txt").write_text("x nosuchproto a.vcd -\n")
    assert main([str(tmp_path / "m.txt")], io.StringIO()) == 2
    (tmp_path / "m.txt").write_text("# only comments\n")
    assert main([str(tmp_path / "m.txt")], io.StringIO()) == 2


def test_runs_as_a_script(tmp_path):
    traces(tmp_path)
    (tmp_path / "m.txt").write_text(GOOD)
    p = subprocess.run([sys.executable, os.path.join(ROOT, "verif", "judges", "run_judges.py"),
                        str(tmp_path / "m.txt")], capture_output=True, text=True, cwd=str(tmp_path))
    assert p.returncode == 0, p.stdout + p.stderr
    assert p.stdout.splitlines()[-1].startswith("PASS: 5 cases")
