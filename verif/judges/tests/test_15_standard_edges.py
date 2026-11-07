# SPDX-License-Identifier: Apache-2.0
"""15: behaviour pinned to the standards' text where a reading could differ.

CAN: stuffing covers SOF through the end of the CRC sequence, so five
equal bits ending on the last CRC bit are followed by a stuff bit before
the CRC delimiter. SWD: the host writes on the falling edge, the target
writes on the rising edge, and both read on the rising edge. PS/2: the
host reads device bits on the falling edge of CLK, the device reads host
bits on the rising edge.
"""
import os
import tempfile

import pytest

from verif.judges import can, ps2, swd
from verif.judges.tests.jwave import (Wave, can_bits, can_wave, ps2_dev, ps2_host,
                                      swd_idle, swd_line_reset, swd_op, swd_wave)
from verif.judges.vcd import read_vcd

RATE = 500000


def tmp_vcd(w, tail):
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    w.write(path, tail=tail)
    return path


def can_judge(levels_list):
    w = Wave()
    w.add("can", 1)
    t = 20e-6
    for lv in levels_list:
        t = can_wave(w, "can", t, lv, RATE) + 3 / RATE
    path = tmp_vcd(w, 40e-6)
    try:
        return can.decode(read_vcd(path, {"rx": "can"}), bitrate=RATE)
    finally:
        os.unlink(path)


def frames_with_stuff_after_crc(level):
    """Data frames (ident, data) whose stuffed CRC ends in five bits of level."""
    found = []
    for ident in range(0x800):
        for data in (b"", b"\x00", b"\xff"):
            full = can_bits(ident, data)
            short = can_bits(ident, data, drop_last_stuff=True)
            if len(full) != len(short):
                stuffed = full[:len(short) - 13 + 1]
                if stuffed[-6:-1] == [level] * 5:
                    found.append((ident, data))
            if len(found) >= 2:
                return found
    return found


@pytest.mark.parametrize("level", [0, 1])
def test_can_stuff_bit_after_the_last_crc_bit(level):
    found = frames_with_stuff_after_crc(level)
    assert found, "no frame with a stuff bit after the CRC"
    ident, data = found[0]
    full = can_bits(ident, data)
    n = len(can_bits(ident, data, drop_last_stuff=True)) - 13
    # bit n is the stuff bit: complement of the five CRC-tail bits before it,
    # then the recessive CRC delimiter
    assert full[n - 5:n] == [level] * 5 and full[n] == 1 - level and full[n + 1] == 1
    fid = "%03X#%s" % (ident, data.hex().upper())
    r = can_judge([full])
    assert r.keys() == [fid] and r.violations == []
    # leaving the stuff bit out is an error: six recessive bits are a stuff
    # error, a dominant run makes the delimiter read as the stuff bit
    r = can_judge([can_bits(ident, data, drop_last_stuff=True)])
    assert r.violations
    assert r.rules()[0] == ("stuff error" if level == 1 else "CRC delimiter")


def swd_judge(cycles, delay):
    w = Wave()
    w.add("swclk", 0)
    w.add("swdio", 1)
    swd_wave(w, cycles, target_delay=delay)
    path = tmp_vcd(w, 1e-6)
    try:
        return swd.decode(read_vcd(path, {"swclk": "swclk", "swdio": "swdio"}, pullup=True))
    finally:
        os.unlink(path)


@pytest.mark.parametrize("delay", [0.0, 0.3, 0.7])
def test_swd_target_bits_read_at_the_next_rising_edge(delay):
    # a target output delay past the falling edge is still read as the host
    # reads it, at the following rising edge
    cyc = (swd_line_reset() + swd_idle() + swd_op(0, 1, 0x0, 0x2BA01477) + swd_idle()
           + swd_op(1, 1, 0x4, ack=2) + swd_idle() + swd_op(1, 0, 0x8, 0x5A5A0F0F) + swd_idle())
    r = swd_judge(cyc, delay)
    assert r.keys() == ["LINE_RESET", "DP R 0x0 OK 2BA01477", "AP R 0x4 WAIT", "AP W 0x8 OK 5A5A0F0F"]
    assert r.violations == []


def ps2_judge(build):
    w = Wave()
    w.add("clk", 1)
    w.add("data", 1)
    build(w)
    path = tmp_vcd(w, 200e-6)
    try:
        return ps2.decode(read_vcd(path, {"clk": "clk", "data": "data"}, pullup=True))
    finally:
        os.unlink(path)


@pytest.mark.parametrize("data_at", [0.1, 0.5, 0.9])
def test_ps2_host_bits_read_on_the_rising_edge(data_at):
    r = ps2_judge(lambda w: ps2_host(w, 100e-6, 0xED, data_at=data_at))
    assert r.keys() == ["host ED"] and r.violations == []


@pytest.mark.parametrize("lag", [5e-6, 20e-6, 35e-6])
def test_ps2_device_bits_read_on_the_falling_edge(lag):
    r = ps2_judge(lambda w: ps2_dev(w, 100e-6, 0x1C, data_lag=lag))
    assert r.keys() == ["dev 1C"] and r.violations == []
