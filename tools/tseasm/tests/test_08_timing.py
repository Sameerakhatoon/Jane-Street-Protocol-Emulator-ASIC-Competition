"""Timing API: the UART and square-wave figures of the fetch-rule model, the phase lemma, errors."""
import pathlib

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools.tseasm import assemble, isa, timing

HERE = pathlib.Path(__file__).parent

UART_LONG_STOP = """
        SET PINS, 1, mask=0b0001
        SET PINDIRS, 0b1101, mask=0b1111
frame:  PULL block
        SET X, 7
        SET PINS, 0, mask=0b0001 side 1
        DLY 6 side 1
bit:    OUT PINS, 1 [6] side 1
        JMP XDEC, bit side 1
        SET PINS, 1, mask=0b0001
        DLY 3
        JMP ALWAYS, frame
"""

SQUARE = """
        SET PINDIRS, 0b0011, mask=0b1111
        DLY 0
top:    DLY 4 side 1
        DLY 2 side 0
        JMP ALWAYS, top side 0
        DLY 0
"""


def uart_bits(words, stop_pcs, lane, lat):
    """Cycles between the TX pin writes (idle, start, 8 data bits, stop) over two frames."""
    frame = [2, 3, 4, 5] + [6, 7] * 8 + stop_pcs
    pcs = [0, 1] + frame + frame + [2, 3, 4]
    r = timing.path(words, pcs, lane, timing.reset_state(lane, 0, lat), waits={2: 1}, fetch_latency=lat)
    writes = [c for pc, c in zip(pcs, r.dispatch) if pc in (0, 4, 6, 8)]
    return [b - a for a, b in zip(writes, writes[1:])]


# 1: UART with the long stop bit: every bit 8 cycles, stop bit 12, in lanes 0 to 2
@pytest.mark.parametrize("lane", [0, 1, 2])
def test_01_uart_long_stop(lane):
    words = assemble(UART_LONG_STOP).words
    assert uart_bits(words, [8, 9, 10], lane, 1) == [8] + ([8] * 9 + [12]) * 2


# 2: UART with stop bit and jump in one word: exactly 8 per bit, 80 per frame; registered
# A_DOUT stretches the last data bit to 12
def test_02_uart_tight():
    words = assemble((HERE / "uart_tx.s").read_text()).words
    assert uart_bits(words, [8, 9], 0, 1) == [8] + [8] * 20
    assert uart_bits(words, [8, 9], 0, 2)[1:11] == [8] * 8 + [12, 8]


# 3: square wave over two words: 9 cycles with the two-word buffer, 12 with one word,
# unchanged with registered A_DOUT
@pytest.mark.parametrize("lane", [0, 1, 2, 3])
def test_03_square_wave(lane):
    words = assemble(SQUARE).words
    for entries, lat, period in ((2, 1, 9), (1, 1, 12), (2, 2, 9)):
        st0 = timing.reset_state(lane, 0, lat)
        pre = timing.path(words, [0, 1], lane, st0, fetch_latency=lat, entries=entries)
        lt = timing.loop(words, [2, 3, 4], lane, pre.end, fetch_latency=lat, entries=entries)
        assert lt.period == [period] and lt.iteration(5) == period
        dispatch = timing.path(words, [2, 3, 4], lane, timing.State(100, (1, 2)), exit_pc=2,
                               fetch_latency=lat, entries=entries).dispatch
        assert dispatch[1] - dispatch[0] == 5


# 4: reset state: the first word is read in the lane's own slot
def test_04_reset_state():
    assert timing.reset_state(0, 6) == timing.State(1, (3,))
    assert timing.reset_state(3, 0, fetch_latency=2) == timing.State(5, (0,))


SLOW = st.sampled_from([isa.encode("DLY", d=d) for d in range(3, 12)]
                       + [isa.encode("OUT", dst="NULL", n=1, delay=dl) for dl in range(3, 8)]
                       + [isa.encode("MOV", dst="X", src="Y", delay=4), isa.encode("TMR", delay=5)])


# 5: phase-independence lemma: if every instruction lasts 4 or more cycles, the path takes the
# same time in every slot phase
@settings(max_examples=200, deadline=None)
@given(st.lists(SLOW, min_size=1, max_size=20), st.integers(0, 3), st.integers(0, 100))
def test_05_phase_independence(words, lane, start):
    words = words + [isa.encode("DLY", d=0)]
    n = len(words) - 1
    cycles = {timing.block(words, 0, n, lane, timing.State(start + ph, (0,))).cycles for ph in range(4)}
    assert len(cycles) == 1


# 6: a loop's lengths repeat; total() adds them up
def test_06_loop_total():
    words = [isa.encode("DLY", d=1), isa.encode("STW", addr=3), isa.encode("JMP", target=0)]
    lt = timing.loop(words, [0, 1, 2], 1, timing.State(0, (0,)))
    assert lt.total(10) == sum(lt.iteration(i) for i in range(10))
    assert sum(lt.period) % isa.DATA_FRAME == 0      # the data slot pins the steady rate


# 7: wrong paths and data-dependent waits are refused
def test_07_errors():
    words = [isa.encode("JMP", target=5), isa.encode("WAIT", pol=1, src="FLAG", idx=7), 0xC0FF]
    s = timing.State(0, (0,))
    with pytest.raises(timing.TimingError, match="cannot continue"):
        timing.path(words, [0, 1], 0, s)
    with pytest.raises(timing.TimingError, match="give waits"):
        timing.path(words, [1], 0, s)
    with pytest.raises(timing.TimingError, match="illegal"):
        timing.path(words, [2], 0, s)
    assert timing.path(words, [1], 0, s, waits={1: 4}).end.cycle == 4
