# SPDX-License-Identifier: Apache-2.0
"""The executable semantics, rule by rule (S1 to S7 in semantics.py)."""
from fractions import Fraction as Fr

from tools.tsec import ast as A
from tools.tsec import semantics as S
from tools.tsec.units import drift_budget, lower_bound, placement, upper_bound

from helpers import FCLK, UART, front, role

BIT = Fr(60_000_000, 115200)          # 520.83 ticks


def frame_of(sym, name):
    r = role(sym, name)
    on = r.states[0].edges[0]
    return S.frame(sym, r, on.event, r.states[1].edges[0].body, FCLK)


def test_s1_rounding_directions():
    assert (lower_bound(Fr(625, 3)), upper_bound(Fr(625, 2))) == (209, 312)
    assert lower_bound(Fr(18)) == 18 and upper_bound(Fr(54)) == 54
    assert placement(BIT) == 521


def test_s2_first_enabled_edge_in_textual_order():
    edges = ["a", "b", "c"]
    assert S.first_enabled(edges, lambda e: e in ("b", "c")) == "b"
    assert S.first_enabled(edges, lambda e: False) is None


def test_s3_owner_of_the_next_start():
    pull = A.Event("named", A.Ident("host"), A.Ident("pull"))
    fall = A.Event("fall", A.Ident("rx"))
    assert S.owner_of_next_start(pull) == "us"
    assert S.owner_of_next_start(fall) == "environment"


def test_s4_s5_uart_tx_obligations():
    f = frame_of(front(UART), "tx_side")
    start, data, stop = f.actions
    assert [a.code for a in f.actions] == ["OB-EDGE"] * 3
    assert start.ideals == [0] and start.tol == BIT / 50
    assert data.ideals == [BIT * (k + 1) for k in range(8)]
    assert data.tol == BIT / 50 and data.gap == BIT
    assert stop.ideals == [9 * BIT]
    assert f.tail_code == "OB-HOLD" and f.tail_limit == BIT - BIT / 50


def test_s5_drift_budget():
    assert drift_budget(Fr(10), Fr(10**6), 50) == Fr(-40)
    assert drift_budget(BIT / 50, BIT, 50) == BIT / 50 - BIT * Fr(50, 10**6)


def test_s6_s7_uart_rx_obligations():
    f = frame_of(front(UART), "rx_side")
    win, data, stop = f.actions
    assert win.code == "OB-WIN" and win.window == (BIT * 2 / 5, BIT * 3 / 5)
    assert win.ideals == [BIT / 2]                    # placed at the window centre
    assert data.code == "OB-SAMP" and data.ideals[0] == BIT / 2 + BIT
    assert stop.gap == BIT / 2 and stop.tol == BIT / 50
    assert f.tail_code == "OB-REARM" and S.rearm_ok(250, f) and not S.rearm_ok(251, f)
    assert len(f.pushes) == 1


def test_s6_sampling_and_response_latency():
    assert S.sample_interval(1) == (1, 2)
    assert S.response_latency(1) == (4, 5)            # a buffered reaction: 4 to 5 ticks
    assert S.response_latency(16) == (19, 20)


def test_s7_hold():
    f = frame_of(front(UART), "tx_side")
    assert S.hold_ok(511, f) and not S.hold_ok(510, f)


def test_time_tolerance_is_absolute():
    sym = front("""protocol p { wire tx : out; role r : controller uses tx { var b : bits[2];
      state i { on host.pull(b) -> f; }
      state f { seq { drive tx = 0 for 1 us ~ 50 ns; drive tx = 1 for 2 us ~ 10 %; } -> i; } } }""")
    f = frame_of(sym, "r")
    assert f.actions[0].tol == 3                      # 50 ns at 60 MHz
    assert f.actions[1].tol == 6                      # 10 % of the 1 us before it
    assert f.actions[1].ideals == [60]
