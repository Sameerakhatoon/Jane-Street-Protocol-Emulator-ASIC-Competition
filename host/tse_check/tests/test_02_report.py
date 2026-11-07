# SPDX-License-Identifier: Apache-2.0
"""Timing compliance: verdicts, the I2C start-hold example, clean traffic, report text."""
import pytest
from hypothesis import given, strategies as st

from conftest import CLOCK, capture, seen
from tse_check import records, report as P

START, SCL_FALL, SCL_RISE, SDA_EDGE, STOP, VIOL = 1, 2, 3, 4, 5, 9

# I2C Standard mode at 60 MHz, in ticks
I2C_SM = [
    P.Obligation("tHD;STA", START, SCL_FALL, min=240),
    P.Obligation("tLOW", SCL_FALL, SCL_RISE, min=282),
    P.Obligation("tHIGH", SCL_RISE, SCL_FALL, min=240),
    P.Obligation("tSU;DAT", SDA_EDGE, SCL_RISE, min=15),
    P.Obligation("tSU;STO", SCL_RISE, STOP, min=240),
    P.Obligation("tBUF", STOP, START, min=282),
]


def transaction(t, bits=9, hd_sta=300.0, low=330.0, high=300.0, su_dat=280.0):
    """Edges of one transaction from true time t: START, bits, STOP; returns (edges, end)."""
    e = [(t, START, 0)]
    t += hd_sta
    e.append((t, SCL_FALL, 0))
    for _ in range(bits):
        e.append((t + low - su_dat, SDA_EDGE, 0))
        t += low
        e.append((t, SCL_RISE, 0))
        t += high
        e.append((t, SCL_FALL, 0))
    t += low
    e.append((t, SCL_RISE, 0))
    t += 300.0
    e.append((t, STOP, 0))
    return e, t


def run(edges, obs=I2C_SM, lane=0):
    return P.check(records.decode(capture(edges, lane))[0], obs)


# 1: the verdict boundaries of a minimum and a maximum
@pytest.mark.parametrize("n,lo,hi,want", [
    (241, 240, None, P.PASS), (240, 240, None, P.POSSIBLE), (239, 240, None, P.VIOLATION),
    (99, None, 100, P.PASS), (100, None, 100, P.POSSIBLE), (101, None, 100, P.VIOLATION),
    (50, 40, 60, P.PASS), (40.5, 40.2, None, P.POSSIBLE), (41.2, 40.2, None, P.PASS),
])
def test_01_judge(n, lo, hi, want):
    assert P.judge(n, lo, hi)[0] == want


# 2: soundness: whatever the sampling phase, PASS means the true interval meets
# the minimum and VIOLATION means it does not
@given(st.floats(0, 1000), st.floats(0, 600), st.integers(1, 600))
def test_02_sound(t0, x, lo):
    n = seen(t0 + x) - seen(t0)
    v, margin = P.judge(n, lo)
    if v == P.PASS:
        assert x >= lo
    if v == P.VIOLATION:
        assert x < lo
    assert margin == n - lo


# 3: the pico-examples START hold, 3.125 us against 4.0 us, is a certain violation
@pytest.mark.parametrize("t0,ticks", [(1000.0, 187), (1000.6, 188)])
def test_03_start_hold(t0, ticks):
    edges, _ = transaction(t0, hd_sta=187.5)
    res = run(edges, lane=1)
    hd = res[0]
    assert (len(hd.meas), hd.verdict(), hd.worst().ticks) == (1, P.VIOLATION, ticks)
    assert [r.verdict() for r in res[1:]] == [P.PASS] * 5
    assert P.overall(res) == P.VIOLATION


# 4: the report line names time, parameter, measured value, limit and lane
def test_04_report_text():
    edges, _ = transaction(1000.6, hd_sta=187.5)
    out = P.lines(run(edges, lane=1), CLOCK)
    assert out[0] == "t=1,189  VIOLATION  tHD;STA  measured 3.133 us  min 4.000 us  (lane 1)"
    row = [x for x in out if x.startswith("tHD;STA ")][0].split()
    assert row[1:4] == ["1", "0", "1"] and row[-1] == "VIOLATION"


# 5: clean traffic over many epochs gives no violation and no possible one
def test_05_clean_traffic():
    edges, t = [], 50.3
    for _ in range(40):
        e, t = transaction(t)
        edges += e
        t += 400.0
    res = run(edges)
    assert P.overall(res) == P.PASS
    assert len(res[0].meas) == 40 and len(res[5].meas) == 39
    assert min(r.worst().margin for r in res) > 0


# 6: boundary table: one tick either side of every limit, at every phase
@pytest.mark.parametrize("name,kw", [("tHD;STA", "hd_sta"), ("tLOW", "low"), ("tHIGH", "high")])
@pytest.mark.parametrize("phase", [0.0, 0.25, 0.5, 0.99])
def test_06_boundary(name, kw, phase):
    ob = [o for o in I2C_SM if o.name == name]
    lim = ob[0].min
    assert run(transaction(100 + phase, **{kw: lim + 1.0})[0], ob)[0].verdict() != P.VIOLATION
    assert run(transaction(100 + phase, **{kw: lim + 2.0})[0], ob)[0].verdict() == P.PASS
    assert run(transaction(100 + phase, **{kw: lim - 1.0})[0], ob)[0].verdict() != P.PASS
    assert run(transaction(100 + phase, **{kw: lim - 2.0})[0], ob)[0].verdict() == P.VIOLATION


# 7: forbid codes: a monitor's own violation event carries the measured ticks
def test_07_forbid():
    evs = [records.Event(10, 1, VIOL, 188), records.Event(20, 0, VIOL, 255), records.Event(30, 1, 7, 0)]
    obs = [P.Obligation("flag", forbid=VIOL, min=240), P.Obligation("lane0", forbid=VIOL, lane=0)]
    a, b = P.check(evs, obs)
    assert [(m.ticks, m.margin, m.saturated) for m in a.meas] == [(188, -52, False), (255, 15, True)]
    assert [m.lane for m in b.meas] == [0] and b.verdict() == P.VIOLATION
    assert "measured at least 4.250 us" in P.lines([a], CLOCK)[1]


# 8: lanes measure independently; a lane filter ignores the others; open starts are counted
def test_08_lanes():
    evs = [records.Event(0, 0, START, 0), records.Event(5, 1, START, 0),
           records.Event(300, 1, SCL_FALL, 0), records.Event(400, 0, SCL_FALL, 0),
           records.Event(500, 2, START, 0)]
    r = P.check(evs, [I2C_SM[0]])[0]
    assert sorted((m.lane, m.ticks) for m in r.meas) == [(0, 400), (1, 295)] and r.open == 1
    r = P.check(evs, [P.Obligation("one", START, SCL_FALL, min=240, lane=1)])[0]
    assert [(m.lane, m.ticks) for m in r.meas] == [(1, 295)]


# 9: a later A restarts the measurement, as the monitor clears its clock
def test_09_restart():
    evs = [records.Event(0, 0, START, 0), records.Event(100, 0, START, 0), records.Event(350, 0, SCL_FALL, 0)]
    assert [m.ticks for m in P.check(evs, [I2C_SM[0]])[0].meas] == [250]


# 10: bad obligations are refused
def test_10_bad_obligations():
    for kw in ({"start": 1}, {"start": 1, "end": 2}, {"start": 1, "end": 2, "min": 5, "max": 4}):
        with pytest.raises(ValueError):
            P.Obligation("x", **kw)
