# SPDX-License-Identifier: Apache-2.0
"""12: USB low-speed judge: NRZI, stuffing, PID, CRC5, CRC16, EOP, broken packets."""
import os
import tempfile

import pytest
from hypothesis import given, strategies as st

from verif.judges import usb
from verif.judges.tests.jwave import (Wave, usb_crc5_bits, usb_crc16_bits, usb_line,
                                      usb_packet_bits, usb_wave)
from verif.judges.vcd import read_vcd

T = 1 / 1.5e6


def lsb_value(bits):
    return sum(b << k for k, b in enumerate(bits))


def byte_bits(data):
    return [(x >> k) & 1 for x in data for k in range(8)]


def judge(packets, gap=4e-6, **kw):
    """packets: list of dicts with usb_packet_bits arguments plus line and wave options."""
    w = Wave()
    w.add("dp", 0)
    w.add("dm", 1)
    t = 5e-6
    for p in packets:
        p = dict(p)
        if p.pop("reset", False):
            w.set("dp", t, 0)
            w.set("dm", t, 0)
            w.set("dm", t + 10e-6, 1)
            t += 10e-6 + gap
            continue
        lopts = {k: p.pop(k) for k in ("stuff_error", "skip_last_stuff") if k in p}
        wopts = {k: p.pop(k) for k in ("se0_bits", "scale", "skew") if k in p}
        sym = usb_line(usb_packet_bits(**p), **lopts) if p else []
        t = usb_wave(w, t, sym, **wopts) + gap
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=5e-6)
        return usb.decode(read_vcd(path, {"dp": "dp", "dm": "dm"}), **kw)
    finally:
        os.unlink(path)


def test_published_crc_check_values():
    # CRC catalogue check values over the ASCII string 123456789 sent
    # least significant bit first: CRC-5/USB 0x19, CRC-16/USB 0xB4C8
    msg = byte_bits(b"123456789")
    assert lsb_value(usb_crc5_bits(msg)) == 0x19
    assert lsb_value(usb_crc16_bits(msg)) == 0xB4C8
    # an IN token to address 3, endpoint 0 carries CRC5 0x0A, and DATA0
    # 00 01 02 03 carries the CRC16 bits 1111011101011110 in wire order
    assert lsb_value(usb_crc5_bits([1, 1, 0, 0, 0, 0, 0] + [0, 0, 0, 0])) == 0x0A
    assert "".join(map(str, usb_crc16_bits(byte_bits(b"\x00\x01\x02\x03")))) == "1111011101011110"


@given(st.lists(st.integers(0, 1), max_size=100))
def test_crc_shift_registers_equal_long_division(bits):
    assert usb.crc5(bits) == lsb_value(usb_crc5_bits(bits))
    assert usb.crc16(bits) == lsb_value(usb_crc16_bits(bits))


def test_sync_is_kjkjkjkk():
    assert usb_line([])[:8] == list("KJKJKJKK")


def test_control_transfer_setup_stage():
    r = judge([dict(name="SETUP", addr=0, endp=0),
               dict(name="DATA0", data=bytes.fromhex("8006000100004000")),
               dict(name="ACK"),
               dict(name="IN", addr=3, endp=1),
               dict(name="NAK"),
               dict(name="DATA1", data=b""),
               dict(name="STALL")])
    assert r.keys() == ["SETUP 0.0", "DATA0 8006000100004000", "ACK", "IN 3.1", "NAK", "DATA1", "STALL"]
    assert r.violations == []


def test_stuffing_with_long_runs_of_ones():
    r = judge([dict(name="DATA0", data=b"\xff" * 8), dict(name="OUT", addr=127, endp=15)])
    assert r.keys() == ["DATA0 FFFFFFFFFFFFFFFF", "OUT 127.15"] and r.violations == []


def test_keepalive_reset_and_sof():
    r = judge([dict(reset=True), dict(), dict(name="SOF", frame=0x710)])
    assert r.keys() == ["RESET", "KEEPALIVE", "SOF 710"] and r.violations == []


def test_transition_skew_is_not_a_line_state():
    r = judge([dict(name="IN", addr=5, endp=2, skew=40e-9)])
    assert r.keys() == ["IN 5.2"] and r.violations == []


def test_bad_pid_is_caught():
    r = judge([dict(name="ACK", bad_pid=True), dict(name="NAK")])
    assert r.keys() == ["ERROR", "NAK"] and r.rules() == ["PID check"]


def test_crc5_error_is_caught():
    r = judge([dict(name="IN", addr=9, endp=1, flip_crc=True)])
    assert r.keys() == ["IN 9.1"] and r.rules() == ["CRC5"]


def test_crc16_error_is_caught():
    r = judge([dict(name="DATA1", data=b"\x12\x34", flip_crc=True)])
    assert r.keys() == ["DATA1 1234"] and r.rules() == ["CRC16"]


def test_stuff_error_is_caught():
    r = judge([dict(name="DATA0", data=b"\xff\x00", stuff_error=True), dict(name="ACK")])
    assert r.keys() == ["ERROR", "ACK"] and r.rules()[0] == "stuff error"


def test_stuff_bit_required_before_eop():
    # a payload ending in ones that leaves six ones before EOP: the stuff bit
    # must still be sent (USB 2.0 bit stuffing rule)
    data = None
    for v in range(1 << 16):
        cand = v.to_bytes(2, "little")
        bits = usb_packet_bits("DATA0", data=cand)
        if len(usb_line(bits)) != len(usb_line(bits, skip_last_stuff=True)):
            data = cand
            break
    assert data is not None
    assert judge([dict(name="DATA0", data=data)]).violations == []
    r = judge([dict(name="DATA0", data=data, skip_last_stuff=True)])
    assert r.rules() == ["stuff error"]


@pytest.mark.parametrize("se0_bits,bad", [(2.0, False), (1.9, False), (1.2, True), (1.0, True), (2.5, True)])
def test_eop_width(se0_bits, bad):
    r = judge([dict(name="ACK", se0_bits=se0_bits)])
    assert r.keys() == ["ACK"]
    assert ("EOP width" in r.rules()) == bad


def test_payload_longer_than_low_speed_limit():
    r = judge([dict(name="DATA0", data=bytes(9))])
    assert r.rules() == ["low-speed payload"]


@pytest.mark.parametrize("scale,bad", [(1.01, False), (0.99, False), (1.025, True), (0.975, True)])
def test_bit_rate_tolerance(scale, bad):
    r = judge([dict(name="DATA0", data=bytes.fromhex("A55A0FF0"), scale=scale, se0_bits=2.0 / scale)])
    assert r.keys() == ["DATA0 A55A0FF0"]
    assert ("bit rate" in r.rules()) == bad


@given(st.lists(st.one_of(
    st.builds(lambda a, e: dict(name="IN", addr=a, endp=e), st.integers(0, 127), st.integers(0, 15)),
    st.builds(lambda d: dict(name="DATA0", data=d), st.binary(max_size=8)),
    st.just(dict(name="ACK"))), min_size=1, max_size=4), st.floats(0.99, 1.01))
def test_random_packets(packets, scale):
    r = judge([dict(p, scale=scale, se0_bits=2.0 / scale) for p in packets])
    want = []
    for p in packets:
        if p["name"] == "IN":
            want.append("IN %d.%d" % (p["addr"], p["endp"]))
        elif p["name"] == "ACK":
            want.append("ACK")
        else:
            want.append(("DATA0 " + p["data"].hex().upper()).rstrip())
    assert r.keys() == want and r.violations == []
