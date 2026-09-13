from tools.tsec.lower import Lowering
from tools.tsec.schedule import resolve_labels, solve
from tools.tsec.timing import FetchModel, analyse, base_duration, first_slot, next_dispatch

from helpers import FCLK, UART, front, role

FM = FetchModel()


def test_first_slot():
    assert [first_slot(c, 4, 1) for c in range(6)] == [1, 1, 5, 5, 5, 5]


def test_buffer_hit_costs_nothing_and_a_miss_waits_for_the_slot():
    buf = [0]
    assert next_dispatch(10, 1, 1, buf, 0, FM) == 11        # same word: hit
    assert next_dispatch(11, 1, 2, buf, 0, FM) == 13        # word 1 miss: slot 12, lat 1
    assert buf == [1, 0]
    assert next_dispatch(13, 1, 0, buf, 0, FM) == 14        # word 0 still buffered
    assert next_dispatch(14, 1, 4, buf, 0, FM) == 17        # miss evicts the older word
    assert buf == [2, 0]


def test_long_instruction_hides_the_fetch():
    assert next_dispatch(0, 9, 2, [0], 0, FM) == 9


def test_durations():
    assert base_duration("DLY", {"d": 5}, 0, 0, FM) == 6
    assert base_duration("OUT", {"delay": 3}, 0, 0, FM) == 4
    assert base_duration("CSRW", {"addr": 0x18}, 0, 0, FM) == 3
    assert base_duration("LDW", {}, 3, 0, FM) == 2           # lane 0 data slot at 3
    assert base_duration("STW", {}, 4, 0, FM) == 16


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
    rep = solve(low, labels, 0, FetchModel(S=3), 50)
    assert rep.violations == []
