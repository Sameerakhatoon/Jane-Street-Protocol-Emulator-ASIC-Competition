# SPDX-License-Identifier: Apache-2.0
"""10: bit stuffing: CAN and USB examples, round trip, run lengths, errors."""
from hypothesis import assume, given, settings, strategies as st

from model.stuffer import Stuffer, StuffIn, destuff, stuff

CAN, USB = (5, 3), (6, 1)


def longest_run(line, mode):
    best = run = 0
    for k, b in enumerate(line):
        run = run + 1 if k and b == line[k - 1] else 1
        if mode & (1 if b else 2):
            best = max(best, run)
    return best


def test_01_can_inserts_after_five_equal_bits_of_either_value():
    assert stuff([0] * 5 + [0, 1], *CAN) == [0] * 5 + [1, 0, 1]
    assert stuff([1] * 6, *CAN) == [1] * 5 + [0, 1]


def test_02_stuff_bit_starts_the_next_run():
    # 00000 (1) 1111 -> the stuffed 1 plus four 1s is a run of five: stuff a 0
    assert stuff([0] * 5 + [1] * 4 + [1], *CAN) == [0] * 5 + [1] + [1] * 4 + [0, 1]


def test_03_usb_counts_ones_only():
    assert stuff([1] * 7, *USB) == [1] * 6 + [0, 1]
    assert stuff([0] * 10, *USB) == [0] * 10


def test_04_n_zero_disables():
    for mode in (1, 2, 3):
        assert stuff([0] * 20 + [1] * 20, 0, mode) == [0] * 20 + [1] * 20


def test_05_wrong_stuff_bit_is_an_error_and_is_dropped():
    data, errs = destuff([0] * 5 + [0] + [1, 1], *CAN)
    assert errs == 1 and data == [0] * 5 + [1, 1]


def test_06_rx_is_registered_one_cycle():
    s = Stuffer(*CAN)
    o1 = s.step(StuffIn(rx_step=1, rx_din=1))
    o2 = s.step(StuffIn())
    o3 = s.step(StuffIn())
    assert (o1["rx_valid"], o2["rx_valid"], o2["rx_dout"], o3["rx_valid"]) == (0, 1, 1, 0)


@settings(max_examples=300, deadline=None)
@given(st.integers(1, 15), st.integers(1, 3), st.lists(st.integers(0, 1), max_size=80))
def test_07_destuff_of_stuff_is_identity_and_runs_are_bounded(n, mode, bits):
    assume(not (n == 1 and mode == 3))       # every bit would be a stuff bit: TX never takes data
    line = stuff(bits, n, mode)
    assert destuff(line, n, mode) == (bits, 0)
    assert longest_run(line, mode) <= n
