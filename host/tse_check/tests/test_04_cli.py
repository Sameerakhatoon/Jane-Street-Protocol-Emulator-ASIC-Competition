"""Obligation files and the command line."""
import json
import os
import subprocess
import sys

import pytest

from conftest import HOST, capture
from tse_check import obfile

OBS = {
    "clock_hz": 60e6,
    "codes": {"start": 1, "scl_fall": 2, "nak": 7},
    "obligations": [
        {"name": "tHD;STA", "from": "start", "to": "scl_fall", "min": "4.0us"},
        {"name": "ack", "forbid": "nak"},
    ],
}


def cli(*args, stdin=None):
    p = subprocess.run([sys.executable, os.path.join(HOST, "tse_check")] + list(args),
                       capture_output=True, text=True, input=stdin)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def obfile_path(tmp_path):
    p = tmp_path / "ob.json"
    p.write_text(json.dumps(OBS))
    return str(p)


def dump(words):
    return "".join("%08X\n" % w for w in words)


# 1: times with units convert to ticks; codes by name or number
def test_01_load():
    clock, obs = obfile.load(json.dumps(OBS))
    assert clock == 60e6 and obs[0].min == 240 and (obs[0].start, obs[0].end) == (1, 2)
    assert obs[1].forbid == 7
    assert obfile.ticks("100ns", 60e6) == 6 and obfile.ticks("250t", 60e6) == 250
    assert obfile.ticks("0.6us", 50e6) == 30 and obfile.ticks("1us", 7e6) == pytest.approx(7.0)
    assert obfile.ticks("10ns", 60e6) == pytest.approx(0.6)


# 2: malformed files are refused with a reason
@pytest.mark.parametrize("bad", [
    "{", "{}", '{"clock_hz": 1}',
    '{"clock_hz": 1, "obligations": [{"name": "x", "from": 1, "to": 99, "min": 1}]}',
    '{"clock_hz": 1, "obligations": [{"name": "x", "from": 1, "to": 2, "min": "4 parsecs"}]}',
    '{"clock_hz": 1, "obligations": [{"name": "x", "from": "nope", "to": 2, "min": 1}]}',
])
def test_02_bad_files(bad):
    with pytest.raises(obfile.ObError):
        obfile.load(bad)


# 3: report on a capture file: the start-hold violation, exit status 1
def test_03_report(tmp_path, obfile_path):
    rec = tmp_path / "cap.txt"
    rec.write_text("# EV_POP dump\n" + dump(capture([(1000.6, 1, 0), (1188.1, 2, 0)])))
    code, out, _ = cli("report", obfile_path, str(rec))
    assert code == 1
    assert out.splitlines()[0] == "t=1,189  VIOLATION  tHD;STA  measured 3.133 us  min 4.000 us  (lane 0)"
    code, out, _ = cli("report", obfile_path, "-", stdin=dump(capture([(10, 1, 0), (300, 2, 0)])))
    assert code == 0 and "VIOLATION" not in out


# 4: sweep through a capture command; the device naks once the hold drops below 270 ticks
def test_04_sweep(tmp_path, obfile_path):
    gen = tmp_path / "gen.py"
    gen.write_text(
        "import sys\n"
        "sys.path[:0] = %r\n"
        "from conftest import capture\n"
        "setup = 300 - int(sys.argv[1])\n"
        "edges = [(10.5, 1, 0), (10.5 + setup, 2, 0), (900, 6 if setup >= 270 else 7, 0)]\n"
        "print(''.join('%%08X\\n' %% w for w in capture(edges)))\n"
        % ([os.path.dirname(os.path.abspath(__file__)), HOST, os.path.dirname(HOST)],))
    code, out, err = cli("sweep", obfile_path, "--cmd", "%s %s {offset}" % (sys.executable, gen),
                         "--from", "0", "--to", "100", "--step", "10")
    assert code == 1, err
    assert "last passing offset: 30 t" in out and "first failing offset: 40 t" in out
    assert "failing edge: ack" in out
    code, out, err = cli("sweep", obfile_path, "--cmd", "false", "--from", "0", "--to", "1")
    assert code == 2 and "capture at offset 0 failed" in err


# 5: usage errors exit 2
def test_05_usage(obfile_path):
    assert cli("report", obfile_path, "/nonexistent")[0] == 2
    assert cli("sweep", obfile_path, "--cmd", "true", "--from", "0", "--to", "1", "--step", "0")[0] == 2
