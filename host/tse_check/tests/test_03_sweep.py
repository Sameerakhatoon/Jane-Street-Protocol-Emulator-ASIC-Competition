"""Margin sweep against devices with hidden thresholds."""
from hypothesis import given, settings, strategies as st

from conftest import CLOCK, capture
from tse_check import report as P, sweep as S

SDA_EDGE, SCL_RISE, ACK, NAK = 4, 3, 6, 7
NOMINAL = 40


def device(hidden, latch=0):
    """Capture of a target that acks only when data setup reaches hidden ticks;
    latch models an input register that costs the device extra ticks."""
    def cap(offset):
        setup = NOMINAL - offset
        edges, t = [], 100.37
        for _ in range(8):
            edges += [(t, SDA_EDGE, 0), (t + setup, SCL_RISE, 0)]
            t += 500
        edges.append((t, ACK if setup - latch >= hidden else NAK, 0))
        return capture(edges)
    return cap


NAK_OB = P.Obligation("ack", forbid=NAK)


# 1: a hidden setup threshold is found exactly, and the failing edge is named
@given(st.integers(3, 35))
@settings(max_examples=30)
def test_01_hidden_threshold(hidden):
    sw = S.run(device(hidden), [NAK_OB], range(0, NOMINAL))
    assert NOMINAL - sw.last_pass == hidden and sw.first_fail == sw.last_pass + 1
    assert sw.failing_edge()[0] == "ack" and not sw.steps[-1].ok
    assert len(sw.steps) == NOMINAL - hidden + 2


# 2: an input register shows as a constant offset, as on real devices
def test_02_latch_offset():
    sw = S.run(device(9, latch=1), [NAK_OB], range(0, NOMINAL))
    assert NOMINAL - sw.last_pass == 10


# 3: with a spec minimum among the obligations the sweep stops at the spec edge
# first and names the setup measurement that failed
def test_03_spec_edge():
    spec = P.Obligation("tSU;DAT", SDA_EDGE, SCL_RISE, min=15)
    sw = S.run(device(5), [spec, NAK_OB], range(0, NOMINAL))
    name, m = sw.failing_edge()
    assert name == "tSU;DAT" and m.ticks == 15 and m.margin == 0
    assert sw.first_fail == NOMINAL - 15
    text = "\n".join(S.lines(sw, CLOCK))
    assert "last passing offset: 24 t" in text and "failing edge: tSU;DAT, lane 0" in text


# 4: the whole range with stop=False, and an ok() callback for device behaviour
def test_04_all_and_callback():
    cap = device(20)
    sw = S.run(cap, [P.Obligation("none", forbid=61)], range(0, NOMINAL),
               ok=lambda evs: all(e.code != NAK for e in evs), stop=False)
    assert len(sw.steps) == NOMINAL and sw.last_pass == NOMINAL - 20
    assert sw.failing_edge() == ("device", None)
    assert [s.ok for s in sw.steps].count(True) == NOMINAL - 20 + 1


# 5: a device that never fails gives no failing edge
def test_05_never_fails():
    sw = S.run(device(0), [NAK_OB], range(0, 10))
    assert sw.first_fail is None and sw.failing_edge() is None and sw.last_pass == 9
