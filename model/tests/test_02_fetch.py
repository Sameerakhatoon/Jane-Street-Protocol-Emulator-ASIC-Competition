"""02: fetch slots and the two-word buffer: the published UART and square-wave timings."""
import pytest

from model.fetch import Fetch, first_data_slot, first_owned
from model.tests.helpers import E, SeqCfg, dispatch_cycles, run_seq, runs

UART_TIGHT = [0x4011, 0x41FD, 0xC300, 0x4207, 0x4810, 0xE806, 0x8806, 0x0A06, 0x4011, 0x0002]
UART = UART_TIGHT[:9] + [E("DLY", d=3), E("JMP", cond=0, target=2)]
SQUARE = [E("SET", dst=1, imm=0xF3), E("DLY"), E("DLY", ss=1, d=4), E("DLY", d=2),
          E("JMP", cond=0, target=2), E("DLY")]
UART_CFG = SeqCfg(side_en=1, ss_base=2)
SQ_CFG = SeqCfg(side_en=1, ss_base=0)

LAYOUT1 = [(1, 8), (0, 8), (1, 8), (0, 8), (1, 8), (0, 8), (1, 8), (0, 8), (1, 8), (0, 8),
           (1, 12), (0, 8), (1, 16), (0, 24), (1, 8), (0, 8)]
LAYOUT2 = LAYOUT1[:10] + [(1, 8)] + LAYOUT1[11:]


def uart_runs(prog, lane, **kw):
    _, pins, _ = run_seq(prog, 200, lane, UART_CFG, data=[0x55, 0xA3], **kw)
    return runs(pins)[:16]


def test_slot_arithmetic():
    assert [first_owned(c, 2) for c in range(6)] == [2, 2, 2, 6, 6, 6]
    assert first_data_slot(0, 0) == 3 and first_data_slot(4, 0) == 19
    assert first_data_slot(0, 2) == 11 and first_data_slot(12, 2) == 27


def test_lru_keeps_the_current_word():
    f = Fetch(0)
    f.need(0, 0)
    f.need(1, 1)
    f.need(2, 2)
    assert f.tags == [2, 1]


@pytest.mark.parametrize("lane", [0, 1])
def test_uart_layout1(lane):
    assert uart_runs(UART, lane) == LAYOUT1


def test_uart_layout2():
    assert uart_runs(UART_TIGHT, 0) == LAYOUT2


def test_uart_layout2_registered_dout_last_data_bit():
    r = uart_runs(UART_TIGHT, 0, lat=2)
    assert r[8:10] == [(1, 8), (0, 12)]


@pytest.mark.parametrize("entries, lat, want", [
    (1, 1, [(1, 5), (0, 7)] * 3),
    (2, 1, [(1, 5), (0, 4)] * 3),
    (2, 2, [(1, 5), (0, 4)] * 3),
])
def test_square_wave(entries, lat, want):
    _, pins, _ = run_seq(SQUARE, 80, 1, SQ_CFG, entries=entries, lat=lat)
    assert runs(pins)[2:8] == want


def test_run_dispatches_first_instruction_at_lane_plus_one():
    for lane in range(2):
        _, _, disp = run_seq([E("DLY")] * 4, 8, lane)
        assert dispatch_cycles(disp)[0] == (lane + 1, 0)
