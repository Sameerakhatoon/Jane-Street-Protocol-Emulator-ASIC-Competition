# SPDX-License-Identifier: Apache-2.0
"""04: I2C judge: transactions, stretching, NACK and every timing limit."""
import os
import tempfile

import pytest
from hypothesis import given, strategies as st

from verif.judges import i2c
from verif.judges.tests.jwave import FM, SM, I2c, Wave
from verif.judges.vcd import read_vcd


def judge(w, **kw):
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=20e-6)
        return i2c.decode(read_vcd(path, {"scl": "scl", "sda": "sda"}, pullup=True), **kw)
    finally:
        os.unlink(path)


def gen(timing=SM, **over):
    w = Wave()
    return w, I2c(w, dict(timing, **over))


def test_write_then_read_with_repeated_start():
    w, g = gen()
    g.transfer(0x50, write=[0x10], read=[0x7F, 0x80])
    g.transfer(0x3C, write=[0x00, 0xAE])
    r = judge(w)
    assert r.keys() == ["S 50W A 10 A Sr 50R A 7F A 80 N P", "S 3CW A 00 A AE A P"]
    assert r.violations == []


def test_fast_mode():
    w, g = gen(FM)
    g.transfer(0x68, write=[0x75], read=[0x71])
    assert judge(w, speed="fm").violations == []
    slow = judge(w, speed="sm")
    assert {"tLOW", "tHIGH", "tHD;STA"} <= set(slow.rules())


def test_ten_bit_address_prefix():
    w, g = gen()
    g.start()
    g.byte(0xF2)      # 11110 + A9 A8 = 01 + W
    g.byte(0x34)
    g.stop()
    assert judge(w).keys() == ["S 10b:1W A 34 A P"]


def test_clock_stretching_is_not_a_violation():
    w, g = gen()
    g.start()
    g.byte(0x50 << 1)
    g.byte(0x10, stretch=200e-6)
    g.stop()
    r = judge(w)
    assert r.keys() == ["S 50W A 10 A P"] and r.violations == []
    assert "205 us" in r.frames[0].info


def test_missing_ack():
    w, g = gen()
    g.start()
    g.byte(0x50 << 1, ack=False)
    g.stop()
    assert judge(w).keys() == ["S 50W N P"]
    assert judge(w, require_ack=True).rules() == ["NACK"]


def test_nack_on_last_read_byte_is_normal():
    w, g = gen()
    g.transfer(0x50, read=[0x01, 0x02])
    assert judge(w, require_ack=True).violations == []


def test_byte_interrupted_by_stop():
    w, g = gen()
    g.start()
    for b in (1, 0, 1, 0):
        g.bit(b)
    g.stop()
    assert "byte interrupted" in judge(w).rules()


@pytest.mark.parametrize("rule,over", [
    ("tHD;STA", dict(thd_sta=3.0e-6)),
    ("tLOW", dict(tlow=4.0e-6, thigh=6.0e-6)),
    ("tHIGH", dict(thigh=3.5e-6, tlow=6.5e-6)),
    ("tSU;STA", dict(tsu_sta=4.0e-6)),
    ("tSU;DAT", dict(tsu_dat=200e-9)),
    ("tSU;STO", dict(tsu_sto=3.0e-6)),
    ("tBUF", dict(tbuf=3.0e-6)),
    ("SCL period (fSCL)", dict(tlow=4.7e-6, thigh=4.0e-6)),
])
def test_each_standard_mode_limit(rule, over):
    w, g = gen(**over)
    g.transfer(0x50, write=[0x10], read=[0x7F])
    g.transfer(0x51, write=[0x22])
    r = judge(w)
    assert set(r.rules()) == {rule}
    v = r.violations[0]
    assert v.measured and v.limit.startswith(">= ")


def test_hold_time_against_a_device_minimum():
    # the specification minimum is 0; a device datasheet may ask for 300 ns
    w, g = gen(thd_dat=100e-9)
    g.transfer(0x50, write=[0x55])
    assert judge(w).violations == []
    assert set(judge(w, thd_dat=300e-9).rules()) == {"tHD;DAT"}


def test_simultaneous_edges_are_data_changes():
    # SDA changing exactly when SCL rises: tSU;DAT is 0, not a false START/STOP
    w, g = gen(tsu_dat=0.0)
    g.transfer(0x50, write=[0xAA])
    r = judge(w)
    assert r.keys() == ["S 50W A AA A P"]
    assert set(r.rules()) == {"tSU;DAT"}


NAMES = ["thd_sta", "tlow", "thigh", "tsu_sta", "tsu_dat", "tsu_sto", "tbuf"]
SM_MIN = dict(thd_sta=4.0e-6, tlow=4.7e-6, thigh=4.0e-6, tsu_sta=4.7e-6, tsu_dat=250e-9,
              tsu_sto=4.0e-6, tbuf=4.7e-6)


def random_timing(draw_u, broken=None):
    p = {k: SM_MIN[k] * (1.0 + draw_u[n]) for n, k in enumerate(NAMES)}
    if broken:
        p[broken] = SM_MIN[broken] * 0.8
    if broken != "thigh":
        p["thigh"] = max(p["thigh"], 10e-6 - p["tlow"])
    else:
        p["tlow"] = max(p["tlow"], 10e-6 - p["thigh"])
    p["thd_dat"] = 0.1e-6
    return p


@given(st.lists(st.floats(0.0, 1.0), min_size=7, max_size=7),
       st.lists(st.integers(0, 255), min_size=1, max_size=4), st.integers(0x08, 0x77))
def test_random_timings_above_the_limits_pass(u, data, addr):
    w, g = gen(random_timing(u))
    g.transfer(addr, write=data, read=data[:1])
    g.transfer(addr, write=data[1:] or [0])
    r = judge(w)
    assert r.violations == []
    assert r.keys()[0].startswith("S %02XW A" % addr)


@given(st.lists(st.floats(0.0, 1.0), min_size=7, max_size=7), st.sampled_from(NAMES))
def test_random_single_broken_limit_is_caught(u, broken):
    w, g = gen(random_timing(u, broken))
    g.transfer(0x50, write=[0x10, 0x20], read=[0x30])
    g.transfer(0x50, write=[0x40])
    want = {"thd_sta": "tHD;STA", "tlow": "tLOW", "thigh": "tHIGH", "tsu_sta": "tSU;STA",
            "tsu_dat": "tSU;DAT", "tsu_sto": "tSU;STO", "tbuf": "tBUF"}[broken]
    assert want in judge(w).rules()
