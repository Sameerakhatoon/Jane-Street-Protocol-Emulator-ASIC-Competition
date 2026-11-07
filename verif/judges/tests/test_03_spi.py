# SPDX-License-Identifier: Apache-2.0
"""03: SPI judge in all four modes, with framing and setup/hold faults."""
import os
import tempfile

import pytest
from hypothesis import given, strategies as st

from verif.judges import spi
from verif.judges.tests.jwave import Wave, spi_xfer
from verif.judges.vcd import read_vcd


def bus(mode, lines=("mosi", "miso"), cs=True):
    w = Wave()
    w.add("clk", mode >> 1)
    for name in lines:
        w.add(name, 0)
    if cs:
        w.add("cs", 1)
    return w


def judge(w, roles=("clk", "mosi", "miso", "cs"), **kw):
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=5e-6)
        tr = read_vcd(path, {r: r for r in roles if r in w.sigs})
        return spi.decode(tr, **kw)
    finally:
        os.unlink(path)


@pytest.mark.parametrize("mode", range(4))
def test_modes(mode):
    w = bus(mode)
    t = spi_xfer(w, 1e-6, [0x9F, 0x00, 0x00], [0xFF, 0xEF, 0x40], mode=mode)
    spi_xfer(w, t + 2e-6, [0x03], [0xA5], mode=mode)
    r = judge(w, mode=mode)
    assert r.keys() == ["mosi=9F,00,00 miso=FF,EF,40", "mosi=03 miso=A5"]
    assert r.violations == []


@pytest.mark.parametrize("mode", range(4))
def test_wrong_mode_is_noticed(mode):
    w = bus(mode)
    spi_xfer(w, 1e-6, [0x9F, 0x35], [0x12, 0x34], mode=mode)
    r = judge(w, mode=mode ^ 2)     # wrong CPOL
    assert "clock idle at CS assert" in r.rules()


def test_lsb_first_and_16_bit_words():
    w = bus(0)
    spi_xfer(w, 1e-6, [0x1234], [0xBEEF], bits=16, lsb_first=True)
    r = judge(w, bits=16, lsb_first=True)
    assert r.keys() == ["mosi=1234 miso=BEEF"]


def test_without_chip_select():
    w = bus(3, lines=("mosi",), cs=False)
    spi_xfer(w, 1e-6, [0xC3, 0x3C], [], mode=3, cs=False)
    r = judge(w, mode=3)
    assert r.keys() == ["mosi=C3,3C"]


def test_incomplete_word_is_caught():
    w = bus(0)
    spi_xfer(w, 1e-6, [0xAB], [0xCD], bits=8)
    r = judge(w, bits=16)
    assert r.rules() == ["mosi incomplete word", "miso incomplete word"]


def test_setup_violation_is_caught():
    # mode 0 data launched 450 ns late with a 1 us clock: 50 ns of setup before the rising edge
    w = bus(0, lines=("mosi",))
    spi_xfer(w, 1e-6, [0x5A], [], period=1e-6, shift=450e-9)
    ok = judge(w, tsu=40e-9)
    bad = judge(w, tsu=100e-9)
    assert ok.violations == [] and set(bad.rules()) == {"mosi tSU"}


def test_hold_violation_is_caught():
    # mode 1 samples on the falling edge; launching 480 ns early puts each data
    # change 20 ns after the previous sampling edge
    w = bus(1, lines=("mosi",))
    spi_xfer(w, 1e-6, [0x5A], [], mode=1, period=1e-6, shift=-480e-9)
    bad = judge(w, mode=1, thd=50e-9)
    assert "mosi tHD" in bad.rules()


@given(st.integers(0, 3), st.lists(st.integers(0, 255), min_size=1, max_size=6),
       st.sampled_from([100e-9, 1e-6, 10e-6]))
def test_random_modes_and_payloads(mode, data, period):
    w = bus(mode)
    miso = [(b * 7 + 3) & 0xFF for b in data]
    spi_xfer(w, 1e-6, data, miso, mode=mode, period=period)
    r = judge(w, mode=mode, tsu=period / 4, thd=period / 4)
    assert r.keys() == ["mosi=%s miso=%s" % (",".join("%02X" % b for b in data),
                                             ",".join("%02X" % b for b in miso))]
    assert r.violations == []
