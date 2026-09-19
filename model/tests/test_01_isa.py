"""01: the ISA transcription: legal word count, round trip, published encodings."""
import pytest

from model.isa import Illegal, decode, encode, pack


def legal(w):
    try:
        decode(w)
        return True
    except Illegal:
        return False


def test_exhaustive_legal_count():
    assert sum(legal(w) for w in range(1 << 16)) == 55320


def test_every_legal_word_round_trips():
    for w in range(1 << 16):
        if legal(w):
            name, ss, f = decode(w)
            assert encode(name, ss, **f) == w


@pytest.mark.parametrize("word, name, ss, fields", [
    (0x0A06, "JMP", 1, dict(cond=2, target=6)),
    (0x8006, "OUT", 0, dict(dst=0, n1=0, delay=6)),
    (0xE62A, "EMIT", 0, dict(code=5, delay=2)),
    (0x24A8, "WAIT", 0, dict(pol=1, src=0, idx=5, to=0, alt=1, delay=0)),
    (0xC780, "LDW", 0, dict(ind=1, addr=0)),
    (0xC300, "PULL", 0, dict(blk=1)),
])
def test_published_encodings(word, name, ss, fields):
    assert decode(word) == (name, ss, fields)
    assert encode(name, ss, **fields) == word


@pytest.mark.parametrize("word", [
    0x8600,   # OUT selector 6
    0xA700,   # IN selector 7
    0xC001,   # PUSH with a reserved bit
    0xC781,   # LDW X with a nonzero address
    0xE500,   # SIG with bit 8 set
])
def test_illegal_words(word):
    with pytest.raises(Illegal):
        decode(word)


def test_uart_example_packs_like_d51():
    prog = [0x4011, 0x41FD, 0xC300, 0x4207, 0x4810, 0xE806, 0x8806, 0x0A06, 0x4011, 0x0002]
    assert pack(prog) == [0x41FD4011, 0x4207C300, 0xE8064810, 0x0A068806, 0x00024011]
