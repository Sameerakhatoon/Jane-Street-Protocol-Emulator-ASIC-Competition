"""Runtime SPI transactions and event records."""
import pytest
from hypothesis import given, strategies as st

from tse_host import spi as S
from tse_host import frames as F

addr_st = st.integers(0, 0xFF00)
word_st = st.integers(0, 0xFFFFFFFF)


# 1: transaction layouts: command, address MSB first, dummy byte before read data
def test_01_layout():
    assert S.write(0x4001, 0x7) == (b"\x01\x40\x01\x00\x00\x00\x07", 0)
    assert S.read(0x4002) == (b"\x02\x40\x02\x00", 4)
    assert S.read_block(0x3002, 3) == (b"\x04\x30\x02\x00\x03\x00", 12)
    assert S.write_block(0x10, [1]) == (b"\x03\x00\x10\x00\x01\x00\x00\x00\x01", 0)
    assert S.status() == (b"\x13", 1)
    assert S.control(F.COMMIT, F.be32(5)) == (b"\x12\x00\x00\x00\x05", 0)
    with pytest.raises(ValueError):
        S.control(F.WR)


# 2: encode then parse gives back the request
@given(addr_st, st.lists(word_st, min_size=1, max_size=20))
def test_02_round_trip(addr, words):
    assert S.parse(S.write(addr, words[0])[0]) == (F.WR, addr, 1, words[:1])
    assert S.parse(S.read(addr)[0]) == (F.RD, addr, 1, [])
    assert S.parse(S.write_block(addr, words)[0]) == (F.WRB, addr, len(words), words)
    assert S.parse(S.read_block(addr, len(words))[0])[:3] == (F.RDB, addr, len(words))
    assert S.words(F.words_bytes(words)) == words


# 3: malformed transactions are refused, not guessed
@pytest.mark.parametrize("mosi", [b"", b"\x01\x00", b"\x01\x00\x00\x00", b"\x03\x00\x00\x00\x02\x00",
                                  b"\x02\x00\x00", b"\x13\x00", b"\x7f\x00\x00"])
def test_03_malformed(mosi):
    with pytest.raises(ValueError):
        S.parse(mosi)


# 4: event records and the TIME record that rebuilds 32-bit timestamps
def test_04_events():
    rec = (2 << 30) | (0x05 << 24) | (0xAB << 16) | 0x1234
    assert S.event(rec) == (2, 5, 0xAB, 0x1234)
    time = (0 << 30) | (S.EV_TIME << 24) | 0x0003
    out, epoch = S.timeline([rec, time, rec])
    assert out == [(0x1234, 2, 5, 0xAB), (0x31234, 2, 5, 0xAB)] and epoch == 3
    out, _ = S.timeline([rec], epoch)
    assert out == [(0x31234, 2, 5, 0xAB)]
