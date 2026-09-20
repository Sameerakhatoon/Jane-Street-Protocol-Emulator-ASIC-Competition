"""Random images through the byte bus with random strobe spacing, and random damage."""
import random

from hypothesis import given, settings, strategies as st

from tse_host import frames as F
from tse_host import image, model
from tse_host.link import PinLink
from tse_host.session import Session

SRAM = st.lists(st.integers(0, 0xFFFFFFFF), min_size=1, max_size=96)


@st.composite
def loads(draw):
    """Blocks in the program SRAM and lane 0 to 2 configuration, as an image would hold."""
    blocks = []
    for win in draw(st.sets(st.integers(0, 3), min_size=1, max_size=2)):
        words = draw(SRAM)
        blocks.append((0x80 * win, words[:0x80]))
    for lane in draw(st.sets(st.integers(0, 2), max_size=2)):
        blocks.append((0x1000 + 0x100 * lane + 0x18, [draw(st.integers(0, 0x0FFFFFFF))]))
    return blocks


def session(seed, hold):
    chip = model.Chip(seed=seed)
    link = PinLink(chip, hold=hold, read_wait=10, rng=random.Random(seed))
    s = Session(link)
    s.enter_load()
    return chip, link, s


# 1: a random image survives the file format, loads, commits and reads back exactly
@settings(max_examples=60, deadline=None)
@given(loads(), st.integers(0, 1 << 30), st.tuples(st.integers(4, 6), st.integers(6, 14)))
def test_01_random_image_loads(blocks, seed, hold):
    name, clock, again = image.unpack(image.pack(blocks, 60000000, b"r"))
    chip, link, s = session(seed, (min(hold), max(hold)))
    s.load(again, bist=False, verify=True)
    assert s.stat()["loaded"] == 1 and not chip.violations
    for addr, words in again:
        if addr < 0x200:
            assert chip.sram[addr:addr + len(words)] == words


# 2: one corrupted address or data byte anywhere makes COMMIT fail
@settings(max_examples=60, deadline=None)
@given(loads(), st.integers(0, 1 << 30), st.data())
def test_02_corrupt_byte_refused(blocks, seed, data):
    stream, crc = F.load_stream(blocks)
    covered = [i for i, (b, f) in enumerate(stream[:-5]) if not f
               and stream_pos_covered(stream, i)]
    i = data.draw(st.sampled_from(covered))
    flip = data.draw(st.integers(1, 255))
    stream[i] = (stream[i][0] ^ flip, 0)
    chip, link, s = session(seed, 4)
    link.exchange(stream)
    assert s.stat()["loaded"] == 0


# 3: a stream cut anywhere before its last byte never ends loaded
@settings(max_examples=40, deadline=None)
@given(loads(), st.integers(0, 1 << 30), st.data())
def test_03_truncated_never_loads(blocks, seed, data):
    stream, crc = F.load_stream(blocks)
    cut = data.draw(st.integers(1, len(stream) - 1))
    chip, link, s = session(seed, 4)
    link.exchange(stream[:cut])
    assert s.stat()["loaded"] == 0


def stream_pos_covered(stream, i):
    """True if byte i of a load stream is an address or data byte of WR or WRB."""
    j = i
    while not stream[j][1]:
        j -= 1
    code, pos = stream[j][0], i - j - 1
    return code == F.WR or (code == F.WRB and pos not in (2, 3))
