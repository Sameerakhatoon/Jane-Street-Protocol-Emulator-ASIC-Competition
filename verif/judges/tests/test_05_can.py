# SPDX-License-Identifier: Apache-2.0
"""05: CAN judge: frames, destuffing, CRC, ACK slot, form and rate errors."""
import os
import tempfile

import pytest
from hypothesis import given, strategies as st

from verif.judges import can
from verif.judges.tests.jwave import Wave, can_bits, can_wave, crc15_div
from verif.judges.vcd import read_vcd

RATE = 500000


def judge(frames, gap_bits=3, scale=1.0, **kw):
    w = Wave()
    w.add("can", 1)
    t = 20e-6
    for f in frames:
        t = can_wave(w, "can", t, f, RATE, scale) + gap_bits / RATE
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=40e-6)
        return can.decode(read_vcd(path, {"rx": "can"}), **dict(dict(bitrate=RATE), **kw))
    finally:
        os.unlink(path)


@given(st.lists(st.integers(0, 1), min_size=1, max_size=120))
def test_crc_shift_register_equals_long_division(bits):
    assert can.crc15(bits) == crc15_div(bits)


def test_standard_extended_and_remote_frames():
    r = judge([can_bits(0x123, bytes.fromhex("DEADBEEF")),
               can_bits(0x1ABCDEF0, b"\x01\x02", ext=True),
               can_bits(0x7FF, rtr=True, dlc=4),
               can_bits(0x000, b"")])
    assert r.keys() == ["123#DEADBEEF", "1ABCDEF0#0102", "7FF#R4", "000#"]
    assert r.violations == []


def test_long_runs_need_stuffing():
    r = judge([can_bits(0x000, b"\x00" * 8), can_bits(0x7FF, b"\xff" * 8)])
    assert r.keys() == ["000#0000000000000000", "7FF#FFFFFFFFFFFFFFFF"] and r.violations == []


def test_missing_ack_is_caught():
    r = judge([can_bits(0x123, b"\x11", ack=False)])
    assert r.keys() == ["123#11"] and r.rules() == ["ACK"]


def test_crc_error_is_caught():
    r = judge([can_bits(0x123, b"\x11\x22", flip_crc=True)])
    assert r.rules() == ["CRC"]


def test_stuff_error_is_caught():
    r = judge([can_bits(0x000, b"\x00", stuff_error=True), can_bits(0x456, b"\x78")], gap_bits=12)
    assert r.keys() == ["ERROR", "456#78"]
    assert r.rules()[0] == "stuff error"


def test_dominant_eof_bit_is_caught():
    r = judge([can_bits(0x321, b"\x01", bad_eof=True)])
    assert r.rules() == ["EOF"]


@pytest.mark.parametrize("scale,bad", [(1.005, False), (0.995, False), (1.02, True), (0.98, True)])
def test_bit_rate_tolerance(scale, bad):
    r = judge([can_bits(0x2AA, bytes.fromhex("5555AAAA"))], scale=scale)
    assert r.keys() == ["2AA#5555AAAA"]
    assert ("bit rate" in r.rules()) == bad


@given(st.integers(0, 0x7FF), st.binary(max_size=8), st.booleans(), st.floats(0.997, 1.003))
def test_random_frames(ident, data, ext, scale):
    if ext:
        ident = (ident << 18) | (ident * 977 & 0x3FFFF)
    r = judge([can_bits(ident, data, ext=ext), can_bits(0x100, data[:2])], scale=scale)
    fid = ("%08X" if ext else "%03X") % ident
    assert r.keys() == [fid + "#" + data.hex().upper(), "100#" + data[:2].hex().upper()]
    assert r.violations == []
