# SPDX-License-Identifier: Apache-2.0
"""Record layout from the tables, encode and decode, epochs and wrap."""
import pytest
from hypothesis import given, strategies as st

from tse_check import records as R
from tse_host import spi


# 1: widths and reserved codes follow the ISA and register tables
def test_01_layout():
    assert (R.RECORD_BITS, R.LANE_BITS, R.CODE_BITS, R.DATA_BITS, R.TS_BITS) == (32, 2, 6, 8, 16)
    assert (R.CODE_MAX, R.TRIG, R.TIME) == (0x3D, 0x3E, 0x3F)
    assert R.pack(1, 0x15, 0xBC, 0x1234) == 0x55BC1234
    assert R.split(0x55BC1234) == (1, 0x15, 0xBC, 0x1234)


# 2: same field split as the host library's own record helper
@given(st.integers(0, 0xFFFFFFFF))
def test_02_matches_host_library(w):
    assert R.split(w) == spi.event(w)


# 3: encode then decode gives the events back, across many epochs and the
# 32-bit wrap (gaps stay below one counter period, as on a live chip)
gap_st = st.one_of(st.integers(0, 50), st.integers(0, (1 << 32) - (1 << 16) - 1))
ev_st = st.lists(st.tuples(gap_st, st.integers(0, 1), st.integers(0, R.CODE_MAX),
                           st.integers(0, 255)), max_size=40)


def timed(raw):
    out, t = [], 0
    for gap, lane, code, data in raw:
        t += gap
        out.append((t, lane, code, data))
    return out


@given(ev_st)
def test_03_round_trip(raw):
    evs = [R.Event(*t) for t in timed(raw)]
    words = R.encode(evs)
    assert R.decode(words)[0] == evs
    assert sum(1 for w in words if R.split(w)[1] == R.TIME) == len({e.ts >> 16 for e in evs})


# 4: decoding in chunks with the returned epoch equals decoding at once
@given(ev_st, st.integers(0, 40))
def test_04_chunks(raw, cut):
    words = R.encode([R.Event(*t) for t in timed(raw)])
    a, ep = R.decode(words[:cut])
    b, _ = R.decode(words[cut:], ep)
    assert a + b == R.decode(words)[0]


# 5: text input, bad words and out-of-range fields are refused
def test_05_errors():
    assert R.parse_words("# dump\n0x3F000001\n 01020304 0a0b0c0d # two\n") == \
        [0x3F000001, 0x01020304, 0x0A0B0C0D]
    with pytest.raises(R.RecordError):
        R.parse_words("xyz\n")
    with pytest.raises(R.RecordError):
        R.split(1 << 32)
    with pytest.raises(R.RecordError):
        R.pack(4, 0, 0, 0)
    with pytest.raises(R.RecordError):
        R.encode([R.Event(1 << 17, 0, 1, 0), R.Event(5, 0, 1, 0)])


# 6: a TRIG record names the lanes that matched and the stage that fired; the chip has
#    two lanes and three stages, so anything else is not a TRIG record from this chip
def test_07_trig_data():
    assert (R.LANES, R.TRIG_STAGES) == (2, 3)
    assert R.trig(0x12) == (1, 2) and R.trig(0x30) == (3, 0)
    for bad in (0x13, 0x02, 0x41):
        with pytest.raises(R.RecordError):
            R.trig(bad)
