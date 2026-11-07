# SPDX-License-Identifier: Apache-2.0
"""17: 10BASE-T end of frame: TP_IDL accepted, broken ones flagged."""
import os
import tempfile

import pytest

from verif.judges import eth10
from verif.judges.tests.jwave import Wave, eth_frame_bits, eth_wave, manchester_wave
from verif.judges.vcd import read_vcd

SRC = bytes.fromhex("020000000001")
DST = bytes.fromhex("6612B3725B4C")


def frame(last_bit):
    """A frame whose FCS ends in last_bit (the payload is searched for one)."""
    for n in range(256):
        bits = eth_frame_bits(DST, SRC, 0x0800, bytes([n]) * 46)
        if bits[-1] == last_bit:
            return bits
    raise AssertionError("no payload found")


def judge(frames, tail=2e-6, **kw):
    """frames: [(bits, idl)]; idl None means no TP_IDL."""
    w = Wave()
    w.add("td", 0)
    t = 2e-6
    for bits, idl in frames:
        t = eth_wave(w, "td", t, bits, idl) + 9.6e-6
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=tail)
        return eth10.decode(read_vcd(path, {"td": "td"}), **kw)
    finally:
        os.unlink(path)


@pytest.mark.parametrize("last", [0, 1])
@pytest.mark.parametrize("idl", [250e-9, 300e-9, 350e-9, 4.5e-6])
def test_valid_tp_idl_passes(last, idl):
    r = judge([(frame(last), idl), (frame(1 - last), idl)])
    assert len(r.frames) == 2 and "ERROR" not in r.keys()
    assert r.violations == []


@pytest.mark.parametrize("last", [0, 1])
def test_short_tp_idl_is_flagged(last):
    r = judge([(frame(last), 200e-9)])
    assert r.rules() == ["TP_IDL"] and "ERROR" not in r.keys()
    assert r.violations[0].measured == "200 ns"


def test_long_tp_idl_is_flagged():
    r = judge([(frame(1), 6e-6)])
    assert r.rules() == ["TP_IDL"] and r.violations[0].measured == "6 us"


def test_missing_tp_idl_is_flagged():
    r = judge([(frame(0), None)])
    assert r.rules() == ["TP_IDL"] and "missing" in r.violations[0].measured


@pytest.mark.parametrize("tail,bad", [(1e-6, False), (10e-6, True)])
def test_line_still_high_at_the_end_of_the_trace(tail, bad):
    w = Wave()
    w.add("td", 0)
    manchester_wave(w, "td", 2e-6, frame(0), 10e6, "ieee", 1)
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=tail)
        r = eth10.decode(read_vcd(path, {"td": "td"}))
    finally:
        os.unlink(path)
    assert r.rules() == (["TP_IDL"] if bad else [])


def test_missing_sfd_is_still_caught_after_a_good_frame():
    good = frame(1)
    no_sfd = [1, 0] * 40                # preamble only, no 10101011
    r = judge([(good, 300e-9), (no_sfd, 300e-9), (good, 300e-9)])
    assert r.keys()[1] == "ERROR" and r.keys()[0] == r.keys()[2] != "ERROR"
    assert r.rules() == ["SFD"]


def test_idl_limits_are_options():
    r = judge([(frame(1), 300e-9)], idl_min=400e-9)
    assert r.rules() == ["TP_IDL"]
