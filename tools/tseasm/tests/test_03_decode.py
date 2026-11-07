# SPDX-License-Identifier: Apache-2.0
"""Decoder over all 65,536 words, illegal words, costs and data-slot durations."""
from collections import Counter

import pytest

from tools.tseasm import disasm_word, assemble, isa, regs

ALL = [(w, isa.decode(w)) for w in range(1 << 16)]


# 1: legal count per instruction; every legal word re-encodes to itself
def test_01_all_words():
    per = Counter(ins.name for _, ins in ALL if ins)
    assert sum(per.values()) == 55320
    assert per == {"JMP": 8192, "WAIT": 8192, "SET": 8192, "MOV": 8192, "OUT": 6144, "IN": 6144,
                   "PUSH": 8, "PULL": 8, "CSRW": 1024, "STW": 516, "CSRR": 1024, "LDW": 516,
                   "DLY": 2048, "TMR": 2048, "SIG": 1024, "EMIT": 2048}
    for w, ins in ALL:
        if ins:
            assert isa.encode(ins.name, **ins.fields) == w


# 2: illegal words: reserved bits, enum holes, LDW/STW X with an address
@pytest.mark.parametrize("word,why", [
    (0xC001, "PUSH with a reserved bit"),
    (0x8600, "OUT selector 6"),
    (0xA700, "IN selector 7"),
    (0xC781, "LDW X with a nonzero address"),
    (0xC581, "STW X with a nonzero address"),
    (0xE500, "SIG with bit 8 set"),
])
def test_02_illegal(word, why):
    assert isa.decode(word) is None, why


# 3: every word disassembles to text that reassembles to it; .word only where the syntax refuses
def test_03_disasm_every_word():
    raw = Counter()
    for w, ins in ALL:
        text = disasm_word(w)
        assert assemble(text).words == [w], text
        if ins and text.startswith(".word"):
            f = ins.fields
            if ins.name == "CSRW":
                assert not regs.lane_writable(f["addr"])
            elif ins.name == "CSRR":
                assert not regs.lane_readable(f["addr"])
            elif ins.name == "TMR":
                assert not f["gwe"] and f["gsel"]
            else:
                assert ins.name == "EMIT" and f["code"] > 0x3D
            raw[ins.name] += 1
    writable = sum(regs.lane_writable(a) for a in range(256))
    readable = sum(regs.lane_readable(a) for a in range(256))
    assert raw == {"CSRW": 4 * (256 - writable), "CSRR": 4 * (256 - readable),
                   "TMR": 4 * 2 * 3 * 32, "EMIT": 4 * 2 * 8}


# 4: static costs
@pytest.mark.parametrize("text,base,wait,dslot", [
    ("CSRW LANE_CFG", 3, False, None),
    ("CSRW RULE_CLK", 3, False, None),
    ("CSRW TXDATA", 1, False, None),
    ("CSRR STATUS", 1, False, None),
    ("MOV X, C [5]", 6, False, None),
    ("DLY 511", 512, False, None),
    ("WAIT 1, EV, RXFULL [3]", 4, True, None),
    ("PUSH block", 1, True, None),
    ("PULL", 1, False, None),
    ("LDW 5", 1, False, "load"),
    ("STW X", 1, False, "store"),
])
def test_04_cost(text, base, wait, dslot):
    assert isa.cost(isa.decode(assemble(text).words[0])) == (base, wait, dslot)


# 5: LDW and STW durations from the lane's data slot (s mod 16 == 4 x lane + 3)
@pytest.mark.parametrize("lane,c,store,load", [
    (0, 3, 1, 2), (0, 4, 16, 17), (0, 0, 4, 5), (1, 7, 1, 2), (2, 0, 12, 13), (3, 15, 1, 2),
])
def test_05_data_slot(lane, c, store, load):
    assert isa.dslot_cycles("store", c, lane) == store
    assert isa.dslot_cycles("load", c, lane) == load
    assert isa.dslot_cycles("load", c, lane, fetch_latency=2) == load + 1
    assert isa.first_slot(c, lane) % isa.FRAME_SLOTS == lane
