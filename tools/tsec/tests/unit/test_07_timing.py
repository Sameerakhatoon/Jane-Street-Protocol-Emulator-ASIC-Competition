# SPDX-License-Identifier: Apache-2.0
import pytest

from tools.tsec import cycles
from tools.tsec.lower import Lowering
from tools.tsec.schedule import resolve_labels, solve
from tools.tsec.timing import FetchModel, analyse

from helpers import FCLK, UART, front, role

FM = FetchModel()


def step(op, a, c, pc, npc, buf, fm=FM, end=None):
    return cycles.step(op, a, c, pc, npc, buf, 0, fm, end)


def test_start_cycle_is_the_first_own_slot_plus_the_fetch_latency():
    assert [cycles.start_cycle(k, FM) for k in range(3)] == [1, 2, 3]
    assert cycles.start_cycle(1, FetchModel(lat=2)) == 3


def test_buffer_hit_costs_nothing_and_a_miss_waits_for_the_slot():
    buf = [0]
    dly = ("DLY", {"d": 0})
    assert step(*dly, 10, 0, 1, buf) == 11                  # same word: hit
    assert step(*dly, 11, 1, 2, buf) == 13                  # word 1 miss: slot 12, lat 1
    assert buf == [1, 0]
    assert step("JMP", {"cond": "ALWAYS", "target": "x"}, 13, 2, 0, buf) == 14   # word 0 still buffered
    assert step("JMP", {"cond": "ALWAYS", "target": "x"}, 14, 0, 4, buf) == 17   # miss evicts the older
    assert buf == [2, 0]


def test_long_instruction_hides_the_fetch():
    assert step("DLY", {"d": 8}, 0, 1, 2, [0]) == 9


def test_waits_last_until_their_end_plus_the_delay():
    assert step("WAIT", {"pol": 1, "src": "PIN", "idx": 0}, 4, 0, 1, [0]) == 5
    assert step("WAIT", {"pol": 1, "src": "PIN", "idx": 0, "delay": 2}, 4, 0, 1, [0], end=9) == 12


def test_durations():
    assert cycles.duration("DLY", {"d": 5}, 0, 0, FM) == 6
    assert cycles.duration("OUT", {"dst": "PINS", "n": 1, "delay": 3}, 0, 0, FM) == 4
    assert cycles.duration("CSRW", {"addr": 0x18}, 0, 0, FM) == 3
    assert cycles.duration("LDW", {}, 3, 0, FM) == 2           # lane 0 data slot at 3
    assert cycles.duration("STW", {}, 4, 0, FM) == 16


def test_the_slot_period_is_the_tables():
    with pytest.raises(ValueError):
        FetchModel(S=3)


def tx_lowering():
    sym = front(UART)
    low = Lowering(sym, role(sym, "tx_side"), FCLK, "dly")
    low.lower()
    return low, resolve_labels(low.prog)


def test_solved_uart_tx_meets_every_obligation():
    low, labels = tx_lowering()
    rep = solve(low, labels, 0, FM, 50)
    assert rep.violations == [] and rep.unvisited == []
    assert len(rep.rows) == 10 and rep.hold_lo == 521


def test_a_too_short_final_delay_breaks_the_hold():
    low, labels = tx_lowering()
    solve(low, labels, 0, FM, 50)
    assert [i.op for i in low.prog[-3:]] == ["DLY", "DLY", "JMP"]
    low.prog[-3].a["d"] = low.prog[-2].a["d"] = 0  # the stop bit's delay, far too short
    rep = analyse(low, labels, 0, FM, 50)
    assert {v.code for v in rep.violations} == {"R313"}
    assert rep.hold_lo < 510


def test_every_phase_and_both_buffers_are_analysed():
    low, labels = tx_lowering()
    rep = solve(low, labels, 0, FetchModel(lat=2), 50)
    assert rep.violations == []
