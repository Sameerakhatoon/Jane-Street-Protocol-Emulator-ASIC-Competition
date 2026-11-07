# SPDX-License-Identifier: Apache-2.0
"""05: the lane timer, its NCO and the sticky TIMER flag."""
from model.timer import Timer

EN, ON_TICK, EV0 = 1 << 17, 1 << 16, 1 << 18


def flags(t, n, rst_at=(0,), ack_at=()):
    out = []
    for c in range(n):
        t.step(frame_rst=int(c in rst_at), ack=int(c in ack_at))
        out.append(t.flag)
    return out


def test_cycle_count_sets_flag_after_reload_cycles():
    f = flags(Timer(ctl=EN | 5), 12)
    assert f.index(1) == 6                 # load in 0, count 5..1 in 1..5, expired, flag
    assert all(f[6:])                      # sticky


def test_flag_clears_on_ack_and_frame_rst():
    f = flags(Timer(ctl=EN | 3), 12, ack_at=(7,))
    assert f[6] == 1 and f[7] == 0 and f[11] == 0


def test_nco_tick_rate():
    t = Timer(inc=1 << 22, ctl=EN)         # a quarter of the clock
    ticks = 0
    for _ in range(400):
        t.step()
        ticks += t.tick
    assert ticks == 100


def test_counts_ticks_when_on_tick():
    f = flags(Timer(inc=1 << 23, ctl=EN | ON_TICK | 4), 20)
    assert f.index(1) > 8                  # four ticks at half rate take about eight cycles


def test_ev0_select_bit():
    assert Timer(ctl=EV0).ev0_sel == 1 and Timer(ctl=0).ev0_sel == 0
