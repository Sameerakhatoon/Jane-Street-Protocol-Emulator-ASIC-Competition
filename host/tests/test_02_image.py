"""TSEI images: round trip, every corruption detected, built from assembled programs."""
import os

import pytest
from hypothesis import given, strategies as st

from tse_host import image as I
from tse_host import regs_map
from tools.tseasm import assemble, hex32, regs
from tools.tseasm.asm import PAD

HERE = os.path.dirname(os.path.abspath(__file__))
UART = os.path.join(os.path.dirname(HERE), "..", "tools", "tseasm", "tests", "uart_tx.s")


@st.composite
def images(draw):
    """Non-overlapping blocks anywhere on the host bus, a name and a clock."""
    starts = sorted(set(draw(st.lists(st.integers(0, 0xFF00), min_size=1, max_size=5))))
    blocks = []
    for i, a in enumerate(starts):
        room = (starts[i + 1] if i + 1 < len(starts) else 0x10000) - a
        n = draw(st.integers(1, min(room, 70)))
        blocks.append((a, draw(st.lists(st.integers(0, 0xFFFFFFFF), min_size=n, max_size=n))))
    name = draw(st.binary(max_size=40))
    clock = draw(st.sampled_from([60000000, 50000000, 48000000, 40000000]))
    return blocks, clock, name


# 1: write, read back, identical; the CRC is the last four bytes
@given(images())
def test_01_round_trip(img):
    blocks, clock, name = img
    blob = I.pack(blocks, clock, name)
    assert I.unpack(blob) == (name, clock, blocks)
    assert blob[:5] == b"TSEI\x01" and I.crc_of(blob) == I.crc32(blob[:-4])


# 2: any single flipped bit, truncation or extra byte is refused
@given(images(), st.data())
def test_02_corruption_refused(img, data):
    blob = I.pack(*img)
    pos = data.draw(st.integers(0, len(blob) - 1))
    bad = bytearray(blob)
    bad[pos] ^= 1 << data.draw(st.integers(0, 7))
    for b in (bytes(bad), blob[:data.draw(st.integers(0, len(blob) - 1))], blob + b"\x00"):
        with pytest.raises(I.ImageError):
            I.unpack(b)


# 3: the writer refuses images the loader could not carry
def test_03_writer_checks():
    for blocks in ([(0xFFFF, [1, 2])], [(0, [1 << 32])], [(0, [1, 2]), (1, [3])], [(0, [])]):
        with pytest.raises(I.ImageError):
            I.pack(blocks, 60000000)
    with pytest.raises(I.ImageError):
        I.pack([(0, [1])], 60000000, b"x" * 256)


# 4: an assembled program becomes its window, LANE_PROG and its configuration words
def test_04_build_from_program():
    with open(UART) as fh:
        prog = assemble(fh.read())
    prog.window = 2
    prog.start = 1
    blocks = I.build({1: prog}, chip=[("PIN_OPT", 0, 0)], data=[7, 8])
    sram = [int(x, 16) for x in hex32(prog.words).split()]
    assert blocks[0] == (0x100, sram)
    assert I.sram_words(prog.words, 0xE000) == sram and PAD == 0xE000
    want = {regs.host_addr("LANE_PROG", lane=1): regs.pack("LANE_PROG", start_pc=1, window=2),
            regs.host_addr("LANE_CFG", lane=1): 0x60,
            regs.host_addr("PIN_OPT"): 0,
            regs_map.REGIONS["data"]["base"]: 7, regs_map.REGIONS["data"]["base"] + 1: 8}
    got = {a + i: w for a, ws in blocks[1:] for i, w in enumerate(ws)}
    assert got == want
    text = I.show(I.pack(blocks, 60000000, "uart"))
    assert "sram window 2" in text and "lane 1 registers" in text and "xbar" in text


# 5: two lanes in one window, or a program larger than its window, are refused
def test_05_build_checks():
    class P:
        window, start, words, config = 0, 0, [0] * 10, []
    with pytest.raises(I.ImageError):
        I.build({0: P, 1: P})
    big = type("Big", (P,), {"words": [0] * 257})
    with pytest.raises(I.ImageError):
        I.build({0: big})


# 6: the generated register map agrees with the register API it was exported from
def test_06_regs_map_matches():
    for name, r in regs.CHIP.items():
        assert regs_map.CHIP[name]["addr"] == r["addr"] and regs_map.CHIP[name]["mask"] == r["mask"]
    for name in regs.LANE:
        for lane in range(3):
            assert I.lane_addr(lane, name) == regs.host_addr(name, lane=lane)
    assert regs_map.RUN == regs.host_addr("RUN") and regs_map.STATUS == regs.host_addr("STATUS")
