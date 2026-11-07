# SPDX-License-Identifier: Apache-2.0
"""13: Manchester judge: both conventions, burst framing, missing transitions."""
import os
import tempfile

import pytest
from hypothesis import given, strategies as st

from verif.judges import manchester
from verif.judges.tests.jwave import Wave, manchester_wave
from verif.judges.vcd import read_vcd

RATE = 1e6


def judge(bursts, convention="ieee", idle=0, drop=None, scale=1.0, decode_as=None, **kw):
    w = Wave()
    w.add("line", idle)
    t = 10e-6
    for k, bits in enumerate(bursts):
        t = manchester_wave(w, "line", t, bits, RATE, convention, idle,
                            drop if k == 0 else None, scale) + 20e-6
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=10e-6)
        return manchester.decode(read_vcd(path, {"line": "line"}),
                                 **dict(dict(bitrate=RATE, convention=decode_as or convention), **kw))
    finally:
        os.unlink(path)


def bits_of(s):
    return [int(c) for c in s]


def test_ieee_rising_middle_edge_is_one():
    # IEEE 802.3: second half carries the bit, so a lone 1 from a low idle
    # is low then high
    w = Wave()
    w.add("line", 0)
    manchester_wave(w, "line", 1e-6, [1], RATE)
    assert w.sigs["line"][2][1:3] == [(1000, 0), (1500, 1)]


@pytest.mark.parametrize("convention", ["ieee", "thomas"])
@pytest.mark.parametrize("idle", [0, 1])
def test_both_conventions_and_idle_levels(convention, idle):
    seq = "1011001110001011"
    r = judge([bits_of(seq), bits_of("0110")], convention, idle)
    assert r.keys() == [seq, "0110"] and r.violations == []


def test_wrong_convention_inverts_the_bits():
    seq = "1011001110001011"
    r = judge([bits_of(seq)], "ieee", decode_as="thomas")
    assert r.keys() == ["".join("1" if c == "0" else "0" for c in seq)]


def test_byte_formats():
    bits = [(0x55 >> k) & 1 for k in range(8)] + [(0xD5 >> k) & 1 for k in range(8)] + [1, 0, 1]
    assert judge([bits], fmt="lsb").keys() == ["55D5+101"]
    assert judge([bits], fmt="msb").keys() == ["AAAB+101"]


def test_all_equal_bits_use_the_first_edge():
    assert judge([[1] * 12], idle=0).keys() == ["1" * 12]
    assert judge([[0] * 12], idle=1).keys() == ["0" * 12]


@pytest.mark.parametrize("pos", [3, 6, 10])
def test_missing_transition_is_caught(pos):
    seq = bits_of("101100111000101101")
    r = judge([seq, bits_of("1100")], drop=pos)
    assert r.keys() == ["ERROR", "1100"]
    assert r.rules() == ["missing transition"]


@pytest.mark.parametrize("scale,bad", [(1.005, False), (0.995, False), (1.02, True), (0.98, True)])
def test_bit_rate_tolerance(scale, bad):
    r = judge([bits_of("1011001110001011" * 2)], scale=scale)
    assert r.keys() == ["1011001110001011" * 2]
    assert ("bit rate" in r.rules()) == bad


@given(st.lists(st.integers(0, 1), min_size=2, max_size=64), st.sampled_from(["ieee", "thomas"]),
       st.integers(0, 1), st.floats(0.995, 1.005))
def test_random_bursts(bits, convention, idle, scale):
    if len(set(bits)) == 1:
        bits = bits + [1 - bits[0]]
    # a burst whose first bit differs from the idle-side half starts on a
    # boundary edge; the first whole-bit gap fixes the phase
    r = judge([bits], convention, idle, scale=scale)
    assert r.keys() == ["".join(map(str, bits))] and r.violations == []
