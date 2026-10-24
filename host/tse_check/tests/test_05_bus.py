"""One bus watched by two lanes: pairing across lanes and cancel codes."""
import json

import pytest

from tse_check import obfile, records, report as P

SCL_FALL, SCL_RISE, START, STOP = 1, 2, 4, 5


def ev(*items):
    """[(ts, lane, code)] to events."""
    return [records.Event(t, lane, code, 0) for t, lane, code in items]


# 1: the START comes from lane 1, the SCL fall from lane 0: only a bus-wide
# obligation pairs them
def test_01_cross_lane():
    evs = ev((100, 1, START), (288, 0, SCL_FALL))
    same = P.Obligation("tHD;STA", START, SCL_FALL, min=240)
    bus = P.Obligation("tHD;STA", START, SCL_FALL, min=240, same_lane=False)
    assert P.check(evs, [same])[0].meas == []
    m = P.check(evs, [bus])[0].meas
    assert [(x.t0, x.t1, x.lane, x.ticks, x.verdict) for x in m] == [(100, 288, 0, 188, P.VIOLATION)]


# 2: a cancel code ends an open measurement: a STOP between the last SCL rise
# and the next START means that START is not a repeated one
def test_02_cancel():
    evs = ev((0, 0, SCL_RISE), (300, 1, STOP), (700, 1, START),
             (1000, 0, SCL_RISE), (1100, 1, START))
    ob = P.Obligation("tSU;STA", SCL_RISE, START, min=282, same_lane=False, cancel=[STOP])
    m = P.check(evs, [ob])[0].meas
    assert [(x.t0, x.t1, x.ticks, x.verdict) for x in m] == [(1000, 1100, 100, P.VIOLATION)]
    plain = P.Obligation("tSU;STA", SCL_RISE, START, min=282, same_lane=False)
    assert len(P.check(evs, [plain])[0].meas) == 2


# 3: a code that both ends and restarts (the SCL period from rise to rise)
# measures every period, and a cancel at a START skips the one across it
def test_03_period():
    evs = ev((0, 0, SCL_RISE), (600, 0, SCL_RISE), (1150, 1, START), (1500, 0, SCL_RISE),
             (2100, 0, SCL_RISE))
    ob = P.Obligation("period", SCL_RISE, SCL_RISE, min=600, same_lane=False, cancel=[START])
    m = P.check(evs, [ob])[0].meas
    assert [(x.t0, x.ticks) for x in m] == [(0, 600), (1500, 600)]
    assert [x.verdict for x in m] == [P.POSSIBLE, P.POSSIBLE]


# 4: per-lane pairing still keeps two buses apart
def test_04_same_lane_default():
    evs = ev((0, 0, START), (10, 1, START), (300, 1, SCL_FALL), (400, 0, SCL_FALL))
    m = P.check(evs, [P.Obligation("tHD;STA", START, SCL_FALL, min=240)])[0].meas
    assert sorted((x.lane, x.ticks) for x in m) == [(0, 400), (1, 290)]


# 5: obligation files carry same_lane and cancel; bad values are refused
def test_05_file():
    d = {"clock_hz": 60e6, "codes": {"rise": SCL_RISE, "start": START, "stop": STOP},
         "obligations": [{"name": "tSU;STA", "from": "rise", "to": "start", "min": "4.7us",
                          "same_lane": False, "cancel": ["stop"]}]}
    _, obs = obfile.load(json.dumps(d))
    assert (obs[0].same_lane, obs[0].cancel, obs[0].min) == (False, (STOP,), 282)
    for bad in ({"same_lane": "no"}, {"cancel": "stop"}, {"cancel": ["nothing"]}):
        o = dict(d["obligations"][0], **bad)
        with pytest.raises(obfile.ObError):
            obfile.load(json.dumps(dict(d, obligations=[o])))
    with pytest.raises(ValueError):
        P.Obligation("x", forbid=7, cancel=[1])
