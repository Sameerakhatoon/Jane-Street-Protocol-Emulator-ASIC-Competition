"""08: the command line: verdict line, expect mode and exit status."""
import io
import os
import subprocess
import sys

from verif.judges.cli import run
from verif.judges.tests.jwave import Wave, can_bits, can_wave, uart_tx

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def uart_vcd(tmp_path, values=(0x48, 0x69), **kw):
    w = Wave()
    w.add("uo_out0", 1)
    t = 10e-6
    for v in values:
        t = uart_tx(w, "uo_out0", t, v, 115200, **kw) + 10e-6
    return w.write(str(tmp_path / "u.vcd"), tail=100e-6)


def call(argv):
    out = io.StringIO()
    rc = run(argv, out)
    return rc, out.getvalue().splitlines()


def test_pass(tmp_path):
    rc, lines = call(["uart", uart_vcd(tmp_path), "--map", "rx=uo_out0", "--baud", "115200"])
    assert rc == 0
    assert lines[-1] == "PASS uart: 2 frames, 0 violations"
    assert lines[0].split()[-1] == "48"


def test_violation_fails(tmp_path):
    rc, lines = call(["uart", uart_vcd(tmp_path, bad_stop=True), "--map", "rx=uo_out0", "--baud", "115200"])
    assert rc == 1
    assert any(ln.startswith("VIOLATION") and "stop bit: measured 0, limit 1" in ln for ln in lines)
    assert lines[-1].startswith("FAIL uart: 2 frames, 2 violations")


def test_expect_mode(tmp_path):
    path = uart_vcd(tmp_path)
    good = tmp_path / "good.txt"
    good.write_text("# bytes the test sent\n48\n\n69\n")
    bad = tmp_path / "bad.txt"
    bad.write_text("48\n6A\n")
    base = ["uart", path, "--map", "rx=uo_out0", "--baud", "115200", "--quiet"]
    assert call(base + ["--expect", str(good)])[0] == 0
    rc, lines = call(base + ["--expect", str(bad)])
    assert rc == 1
    assert "-6A" in [ln.strip() for ln in lines] and "+69" in [ln.strip() for ln in lines]
    assert "frames differ" in lines[-1]


def test_empty_trace_fails_unless_allowed(tmp_path):
    w = Wave()
    w.add("uo_out0", 1)
    w.set("uo_out0", 1e-6, 1)
    path = w.write(str(tmp_path / "e.vcd"))
    assert call(["uart", path, "--map", "rx=uo_out0", "--baud", "9600"])[0] == 1
    assert call(["uart", path, "--map", "rx=uo_out0", "--baud", "9600", "--allow-empty"])[0] == 0


def test_usage_errors(tmp_path):
    path = uart_vcd(tmp_path)
    assert call(["uart", path, "--map", "bogus=uo_out0", "--baud", "9600"])[0] == 2
    assert call(["uart", path, "--map", "rx=missing", "--baud", "9600"])[0] == 2
    assert call(["i2c", path, "--map", "scl=uo_out0"])[0] == 2
    assert call(["uart", str(tmp_path / "none.vcd"), "--map", "rx=x", "--baud", "9600"])[0] == 2


def test_module_entry_point(tmp_path):
    w = Wave()
    w.add("can", 1)
    can_wave(w, "can", 10e-6, can_bits(0x123, bytes.fromhex("DEADBEEF")), 500000)
    path = w.write(str(tmp_path / "c.vcd"), tail=50e-6)
    p = subprocess.run([sys.executable, "-m", "verif.judges", "can", path, "--map", "rx=can",
                        "--bitrate", "500000"], cwd=ROOT, capture_output=True, text=True)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "123#DEADBEEF" in p.stdout and p.stdout.splitlines()[-1].startswith("PASS can")
