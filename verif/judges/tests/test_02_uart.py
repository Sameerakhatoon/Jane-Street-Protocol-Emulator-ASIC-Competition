"""02: UART judge on bit-banged characters, good and deliberately broken."""
import os
import tempfile

import pytest
from hypothesis import given, strategies as st

from verif.judges import uart
from verif.judges.tests.jwave import Wave, uart_tx
from verif.judges.vcd import read_vcd

BAUD = 115200


def judge(w, roles=("rx",), **kw):
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=200e-6)
        tr = read_vcd(path, {r: r for r in roles})
        return uart.decode(tr, **dict(dict(baud=BAUD), **kw))
    finally:
        os.unlink(path)


def line(values, init=1, gap=20e-6, **kw):
    w = Wave()
    w.add("rx", init)
    t = 10e-6
    for v in values:
        t = uart_tx(w, "rx", t, v, BAUD, **kw) + gap
    return w


def test_8n1_bytes():
    r = judge(line([0x41, 0x00, 0xFF, 0x55, 0x0A]))
    assert r.keys() == ["41", "00", "FF", "55", "0A"]
    assert r.violations == []


def test_back_to_back_without_gaps():
    r = judge(line(range(32), gap=0))
    assert r.keys() == ["%02X" % v for v in range(32)] and r.violations == []


@pytest.mark.parametrize("kw,values,keys", [
    (dict(bits=7, parity="even"), [0x41, 0x7F], ["41", "7F"]),
    (dict(bits=8, parity="odd", stop=2.0), [0x00, 0xA5], ["00", "A5"]),
    (dict(bits=9), [0x1FF, 0x100], ["1FF", "100"]),
    (dict(bits=5, stop=1.5), [0x15, 0x0A], ["15", "0A"]),
    (dict(parity="mark"), [0x12], ["12"]),
    (dict(parity="space"), [0x34], ["34"]),
    (dict(msb_first=True), [0x80, 0x01], ["80", "01"]),
])
def test_variants(kw, values, keys):
    r = judge(line(values, **kw), **kw)
    assert r.keys() == keys and r.violations == []


def test_inverted_line():
    r = judge(line([0x3C], init=0, invert=True), invert=True)
    assert r.keys() == ["3C"] and r.violations == []


def test_both_directions_are_tagged():
    w = Wave()
    w.add("rx", 1)
    w.add("tx", 1)
    uart_tx(w, "rx", 10e-6, 0x61, BAUD)
    uart_tx(w, "tx", 200e-6, 0x41, BAUD)
    r = judge(w, roles=("rx", "tx"))
    assert r.keys() == ["rx 61", "tx 41"]


def test_bad_stop_bit_is_caught():
    w = Wave()
    w.add("rx", 1)
    t = uart_tx(w, "rx", 10e-6, 0x41, BAUD)
    uart_tx(w, "rx", t + 50e-6, 0x42, BAUD, bad_stop=True)
    r = judge(w)
    assert r.keys() == ["41", "42"]
    assert r.rules() == ["stop bit"]


def test_parity_error_is_caught():
    r = judge(line([0x41], parity="even", bad_parity=True), parity="even")
    assert r.rules() == ["parity"]


def test_break():
    r = judge(line([0x00], bad_stop=True))
    assert r.keys() == ["BREAK"] and r.rules() == ["stop bit"]


@pytest.mark.parametrize("scale,bad", [(1.01, False), (0.99, False), (1.03, True), (0.97, True)])
def test_baud_tolerance(scale, bad):
    r = judge(line([0x55, 0x0F], scale=scale))
    assert r.keys() == ["55", "0F"]
    assert ("baud" in r.rules()) == bad


def test_long_first_start_bit_is_caught():
    # 13 instead of 10 units for the start bit, the reset-level bug a pins-only judge exposes
    w = Wave()
    w.add("rx", 1)
    uart_tx(w, "rx", 10e-6, 0x41, BAUD, first_bit=1.3 / BAUD)
    r = judge(w)
    assert {"baud", "bit edge"} & set(r.rules())


def test_line_low_at_start_is_caught():
    w = line([0x41], init=0)
    w.set("rx", 1e-6, 1)
    r = judge(w)
    assert r.rules()[0] == "idle level"


def test_glitch_is_not_a_character():
    w = Wave()
    w.add("rx", 1)
    w.set("rx", 5e-6, 0)
    w.set("rx", 5.5e-6, 1)
    uart_tx(w, "rx", 20e-6, 0x41, BAUD)
    r = judge(w)
    assert r.keys() == ["41"] and r.rules() == ["start bit"]


@given(st.lists(st.integers(0, 255), min_size=1, max_size=12),
       st.floats(0.985, 1.015), st.sampled_from([9600, 115200, 1000000]),
       st.floats(0, 30e-6))
def test_random_payload_and_timing(values, scale, baud, gap):
    w = Wave()
    w.add("rx", 1)
    t = 10e-6
    for v in values:
        t = uart_tx(w, "rx", t, v, baud, scale=scale) + gap
    r = judge(w, baud=baud)
    assert r.keys() == ["%02X" % v for v in values]
    assert r.violations == []
