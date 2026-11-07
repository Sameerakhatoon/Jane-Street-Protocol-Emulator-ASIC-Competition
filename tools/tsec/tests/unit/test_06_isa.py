# SPDX-License-Identifier: Apache-2.0
import pytest

from tools.tseasm import isa as gen
from tools.tsec import isa
from tools.tsec.diag import TseError
from tools.tsec.selftest import checks


def test_selftest_checks_all_pass():
    assert all(ok for _, ok in checks())


@pytest.mark.parametrize("mn, f, word", [
    ("JMP", dict(cond="XDEC", target=6, ss=1), 0x0A06),
    ("OUT", dict(dst="PINS", n=1, delay=6), 0x8006),
    ("WAIT", dict(pol=1, src="PIN", idx=5, alt=1), 0x24A8),
    ("PULL", dict(blk=1), 0xC300),
    ("DLY", dict(d=511), 0xE1FF),
])
def test_encode_decode(mn, f, word):
    assert isa.encode(mn, f) == word
    back_mn, back = isa.decode(word)
    assert back_mn == mn and all(back[k] == v for k, v in f.items())


def test_agrees_with_the_generated_assembler_on_every_word():
    for w in range(1 << 16):
        ours = isa.decode(w)
        assert (ours is not None) == gen.is_legal(w), hex(w)
        if ours is not None:
            assert ours[0] == gen.decode(w).name, hex(w)


def test_field_overflow_is_r302():
    with pytest.raises(TseError) as e:
        isa.encode("DLY", {"d": 512})
    assert e.value.code == "R302"
    with pytest.raises(TseError):
        isa.encode("LDW", {"ind": 1, "addr": 3})


def test_disassembly():
    assert isa.fmt("SET", {"dst": "PINS", "imm": 0x11}) == "SET   PINS, 1, mask=0b0001"
    assert isa.fmt("JMP", {"cond": "ALWAYS", "target": 0}, {0: "idle"}) == "JMP   ALWAYS, idle"
    assert isa.fmt("SET", {"dst": "CMD", "imm": isa.CMD_DPLL_ON}) == "SET   CMD, 0x10"


def test_constants_come_from_the_tables():
    assert (isa.DLY_MAX, isa.MAX_INSTRUCTIONS, isa.EV_BIT_START) == (511, 256, 5)
    assert (isa.FRAME_SLOTS, isa.FETCH_ENTRIES, isa.FETCH_LATENCY) == (4, 2, 1)
    assert 0x18 in isa.CLASS_L and 0x20 not in isa.CLASS_L
