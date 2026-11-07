# SPDX-License-Identifier: Apache-2.0
"""07: PS/2 judge: device and host frames, parity, ACK and clock timing."""
import os
import tempfile

from hypothesis import given, strategies as st

from verif.judges import ps2
from verif.judges.tests.jwave import Wave, ps2_dev, ps2_host
from verif.judges.vcd import read_vcd


def bus():
    w = Wave()
    w.add("clk", 1)
    w.add("data", 1)
    return w


def judge(w, **kw):
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=500e-6)
        return ps2.decode(read_vcd(path, {"clk": "clk", "data": "data"}, pullup=True), **kw)
    finally:
        os.unlink(path)


def test_device_and_host_frames():
    w = bus()
    t = ps2_dev(w, 100e-6, 0x1C)
    t = ps2_dev(w, t + 200e-6, 0xF0)
    t = ps2_host(w, t + 500e-6, 0xED)
    t = ps2_dev(w, t + 500e-6, 0xFA)
    r = judge(w)
    assert r.keys() == ["dev 1C", "dev F0", "host ED", "dev FA"]
    assert r.violations == []


def test_parity_and_stop_errors_are_caught():
    w = bus()
    t = ps2_dev(w, 100e-6, 0x1C, bad_parity=True)
    ps2_dev(w, t + 200e-6, 0x32, bad_stop=True)
    assert judge(w).rules() == ["parity", "stop bit"]


def test_missing_device_ack_is_caught():
    w = bus()
    ps2_host(w, 100e-6, 0xFF, ack=False)
    r = judge(w)
    assert r.keys() == ["host FF"] and r.rules() == ["no ACK"]


def test_host_parity_error_is_caught():
    w = bus()
    ps2_host(w, 100e-6, 0xF4, bad_parity=True)
    assert judge(w).rules() == ["parity"]


def test_clock_too_fast_is_caught():
    w = bus()
    ps2_dev(w, 100e-6, 0x1C, half=20e-6)
    r = judge(w)
    assert r.keys() == ["dev 1C"]
    assert {"CLK low", "CLK high"} <= set(r.rules())
    assert judge(w, timing=False).violations == []


def test_short_request_to_send_is_caught():
    w = bus()
    ps2_host(w, 100e-6, 0xED, rts=80e-6)
    assert judge(w).rules() == ["host request-to-send CLK low"]


def test_data_change_too_close_to_clock_rise_is_caught():
    w = bus()
    ps2_dev(w, 100e-6, 0x55, data_lag=2e-6)
    assert set(judge(w).rules()) == {"DATA after CLK rise"}


@given(st.lists(st.tuples(st.booleans(), st.integers(0, 255)), min_size=1, max_size=5),
       st.floats(31e-6, 49e-6))
def test_random_frames(frames, half):
    w = bus()
    t = 100e-6
    for host, v in frames:
        t = (ps2_host(w, t, v, half=half) if host else ps2_dev(w, t, v, half=half)) + 300e-6
    r = judge(w)
    assert r.keys() == ["%s %02X" % ("host" if h else "dev", v) for h, v in frames]
    assert r.violations == []
