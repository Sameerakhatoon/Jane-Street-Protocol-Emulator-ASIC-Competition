# SPDX-License-Identifier: Apache-2.0
"""14: 10BASE-T judge: preamble, SFD, MAC frame, FCS, size, rate."""
import os
import tempfile
import zlib

import pytest
from hypothesis import given, strategies as st

from verif.judges import eth10
from verif.judges.tests.jwave import Wave, eth_frame_bits, eth_wave
from verif.judges.vcd import read_vcd

BCAST = b"\xff" * 6
SRC = bytes.fromhex("020000000001")


def judge(frames, scale=1.0, drop=None, idl=300e-9, **kw):
    w = Wave()
    w.add("td", 0)
    t = 2e-6
    for k, bits in enumerate(frames):
        t = eth_wave(w, "td", t, bits, idl, drop if k == 0 else None, scale) + 9.6e-6
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=2e-6)
        return eth10.decode(read_vcd(path, {"td": "td"}), **kw)
    finally:
        os.unlink(path)


def key(dst, src, etype, payload):
    body = bytes(dst) + bytes(src) + etype.to_bytes(2, "big") + bytes(payload)
    body += bytes(max(0, 60 - len(body)))
    hx = body.hex().upper()
    return "%s %s %s %s" % (hx[0:12], hx[12:24], hx[24:28], hx[28:])


def test_crc32_check_value_and_zlib():
    assert eth10.crc32(b"123456789") == 0xCBF43926
    assert eth10.crc32(bytes(range(64))) == zlib.crc32(bytes(range(64)))


def test_arp_broadcast_and_a_long_frame():
    arp = bytes.fromhex("0001080006040001020000000001c0a80001000000000000c0a80002")
    big = bytes(range(256)) * 5 + bytes(220)
    r = judge([eth_frame_bits(BCAST, SRC, 0x0806, arp), eth_frame_bits(SRC, BCAST, 0x0800, big)])
    assert r.keys() == [key(BCAST, SRC, 0x0806, arp), key(SRC, BCAST, 0x0800, big)]
    assert r.violations == []


def test_fcs_error_is_caught():
    r = judge([eth_frame_bits(BCAST, SRC, 0x0800, b"\x45" * 46, bad_fcs=True)])
    assert r.rules() == ["FCS"]


def test_short_preamble_and_dribble_bits():
    assert judge([eth_frame_bits(BCAST, SRC, 0x0800, b"", preamble=6)]).rules() == ["preamble"]
    assert judge([eth_frame_bits(BCAST, SRC, 0x0800, b"", dribble=3)]).rules() == ["alignment"]


def test_frame_size_limits():
    long = eth_frame_bits(BCAST, SRC, 0x0800, bytes(1501))
    assert judge([long]).rules() == ["frame size"]
    tagged = eth_frame_bits(BCAST, SRC, 0x8100, bytes(1504))
    assert judge([tagged]).violations == []
    bits = eth_frame_bits(BCAST, SRC, 0x0800, b"")
    runt = bits[:64 + 8 * 40]
    r = judge([runt])
    assert "frame size" in r.rules()


def test_missing_transition_in_a_frame():
    r = judge([eth_frame_bits(BCAST, SRC, 0x0800, b""), eth_frame_bits(SRC, BCAST, 0x0800, b"")], drop=200)
    assert r.keys() == ["ERROR", key(SRC, BCAST, 0x0800, b"")]
    assert r.rules() == ["missing transition"]


@pytest.mark.parametrize("scale,bad", [(1.00005, False), (1.0003, True)])
def test_bit_rate_tolerance(scale, bad):
    r = judge([eth_frame_bits(BCAST, SRC, 0x0800, bytes(range(200)))], scale=scale)
    assert r.keys() == [key(BCAST, SRC, 0x0800, bytes(range(200)))]
    assert ("bit rate" in r.rules()) == bad


@given(st.binary(min_size=6, max_size=6), st.integers(0x0600, 0xFFFF), st.binary(max_size=80))
def test_random_frames(dst, etype, payload):
    if etype == 0x8100:
        etype = 0x0800
    r = judge([eth_frame_bits(dst, SRC, etype, payload)])
    assert r.keys() == [key(dst, SRC, etype, payload)] and r.violations == []
