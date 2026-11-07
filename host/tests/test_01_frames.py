# SPDX-License-Identifier: Apache-2.0
"""Loader command streams: CRC, byte order, coverage and burst splitting."""
import zlib

from hypothesis import given, strategies as st

from tse_host import frames as F

blocks_st = st.lists(
    st.tuples(st.integers(0, 0x0FFF), st.lists(st.integers(0, 0xFFFFFFFF), min_size=1, max_size=150)),
    min_size=1, max_size=4)


# 1: CRC-32/ISO-HDLC check value and equality with zlib
def test_01_crc_check_value():
    assert F.crc32(b"123456789") == 0xCBF43926
    data = bytes(range(256)) * 3
    assert F.crc32(data) == zlib.crc32(data)
    assert F.crc32(data[100:], F.crc32(data[:100])) == zlib.crc32(data)


# 2: every command byte has frame 1, payloads frame 0, fields MSB first
def test_02_command_layout():
    assert F.wr(0x1234, 0xA1B2C3D4) == [(0x01, 1), (0x12, 0), (0x34, 0),
                                       (0xA1, 0), (0xB2, 0), (0xC3, 0), (0xD4, 0)]
    assert F.rd(0x4002) == [(0x02, 1), (0x40, 0), (0x02, 0)] + [F.BARE] * 4
    assert F.wrb(0x10, [1, 2])[:5] == [(0x03, 1), (0, 0), (0x10, 0), (0, 0), (2, 0)]
    assert F.rdb(0x10, 3) == [(0x04, 1), (0, 0), (0x10, 0), (0, 0), (3, 0)] + [F.BARE] * 12
    assert F.stat() == [(0x13, 1), F.BARE]
    assert F.begin() == [(0x10, 1)] and F.bist() == [(0x11, 1)]
    assert F.commit(0xCBF43926) == [(0x12, 1), (0xCB, 0), (0xF4, 0), (0x39, 0), (0x26, 0)]


# 3: the COMMIT CRC is zlib's CRC of the address and data bytes, never counts or commands
@given(blocks_st)
def test_03_commit_crc_coverage(blocks):
    body, crc = F.load_body(blocks)
    want = b"".join(F.be16(a) + F.words_bytes(w) for a, w in F.chunks(blocks))
    assert F.covered(body) == want
    assert crc == zlib.crc32(want)
    stream, crc2 = F.load_stream(blocks, bist_first=True)
    assert crc2 == crc and stream[:2] == [(F.BEGIN, 1), (F.BIST, 1)]
    assert stream[-5:] == F.commit(crc)


# 4: bursts never exceed 64 words and keep addresses contiguous
@given(blocks_st)
def test_04_chunks(blocks):
    for addr, words in blocks:
        parts = F.chunks([(addr, words)])
        assert all(1 <= len(w) <= F.CHUNK for _, w in parts)
        assert [a for a, _ in parts] == list(range(addr, addr + len(words), F.CHUNK))
        assert sum((w for _, w in parts), []) == words


# 5: readback streams know where each burst's bytes are
def test_05_readback_layout():
    stream, where = F.readback([(0x100, list(range(70)))])
    assert where == [(0x100, 64, 5), (0x140, 6, 5 + 5 + 256)]
    seen = [0] * len(stream)
    seen[5:9] = [0xDE, 0xAD, 0xBE, 0xEF]
    assert F.words_from(seen, 5, 1) == [0xDEADBEEF]


# 6: address checks and the status byte
def test_06_limits_and_stat():
    for bad in (lambda: F.wr(0x10000, 0), lambda: F.rdb(0xFFFF, 2), lambda: F.wrb(0, [0] * 0x10000)):
        try:
            bad()
            assert False
        except ValueError:
            pass
    assert F.decode_stat(0x37) == {"loaded": 1, "bist_done": 1, "bist_pass": 1, "host_err": 0,
                                   "halted": 3}
    assert F.describe(F.begin() + F.wrb(0x80, [1, 2]) + F.stat()) == ["BEGIN", "WRB 0x0080 n=2", "STAT"]
