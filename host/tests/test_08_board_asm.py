# SPDX-License-Identifier: Apache-2.0
"""The on-board assembler gives the PC assembler's words on every vector."""
import os
import random

import pytest
from hypothesis import given, settings, strategies as st

from tools.tseasm import asm as pc
from tools.tseasm.disasm import disasm, disasm_word
from tse_rp2 import tse_asm as mp

HERE = os.path.dirname(os.path.abspath(__file__))
TSEASM_TESTS = os.path.join(os.path.dirname(HERE), "..", "tools", "tseasm", "tests")

SLOW_TX = """
.program uart_tx
.window 0
.start 0
.side_set 1 opt
.config DPLL_PERIOD 1042
.define BITS 7
idle:   PULL block
        SET X, BITS
        SET PINS, 0, mask=0b0001
        DLY 64
.align 4
bit:    OUT PINS, 1
        DLY 62 - 1+1
        JMP XDEC, bit
        SET PINS, 1, mask=0b0001 side 1
        WAIT 1, CHIP, CF2, alt [3] side 1
        TMR cclr, gsel=1 [2]
        STW X
        LDW 127
        CSRW LANE_CFG
        CSRR STATUS
        EMIT 0x3D
        .word 0xBEEF
        JMP ALWAYS, idle
"""


def both(text):
    a = pc.assemble(text)
    b = mp.assemble(text)
    return a, b


# 1: the shared example and a program using every operand form
@pytest.mark.parametrize("name", ["uart_tx.s", None])
def test_01_programs(name):
    text = open(os.path.join(TSEASM_TESTS, name)).read() if name else SLOW_TX
    a, b = both(text)
    assert b.words == a.words and b.labels == a.labels and b.config == a.config
    assert (b.name, b.window, b.start, b.side_set) == (a.name, a.window, a.start, a.side_set)
    assert mp.sram_words(b.words) == [int(x, 16) for x in pc.hex32(a.words).split()]


def outcome(fn, text):
    try:
        return fn(text, {})
    except Exception as e:
        return type(e).__name__


# 2: every 16-bit word, as the disassembler spells it, assembles identically (or fails in both)
def test_02_every_word():
    for w in range(0x10000):
        text = disasm_word(w).split(";")[0].strip()
        if text.startswith(".word"):
            continue
        a, b = outcome(pc.encode_line, text), outcome(mp.encode_line, text)
        assert a == b == w, (hex(w), text, a, b)


# 3: random programs with labels, through the disassembler, assemble to the same words
@settings(max_examples=150, deadline=None)
@given(st.lists(st.integers(0, 0xFFFF), min_size=1, max_size=60))
def test_03_random_programs(words):
    text = disasm(words)
    a, b = both(text)
    assert a.words == b.words == words


# 4: the same errors, with the same line numbers
@pytest.mark.parametrize("text", [
    "JMP ALWAYS, nowhere", "SET X, 300", "WAIT 1, PIN", "OUT PINS, 33", "CSRW STATUS",
    "CSRR LANE_CFG", "EMIT 0x3E", "a:\na: DLY 0", ".window 4", ".align 3", "FOO 1",
    "SET PINS, 16", "STW X, 3", "DLY 1 side 9", "MOV X, Y [40]", ".config STATUS 1",
    "SET X, 1, mask=3", "PUSH 1"])
def test_04_errors(text):
    with pytest.raises(pc.AsmError) as ea:
        pc.assemble(text)
    with pytest.raises(mp.AsmError) as eb:
        mp.assemble(text)
    assert ea.value.lineno == eb.value.lineno
    assert str(ea.value).split(":")[0] == str(eb.value).split(":")[0]


# 5: random arithmetic in operands evaluates the same
def test_05_values():
    rng = random.Random(4)
    syms = {"A": 3, "b_2": 10}
    for _ in range(300):
        toks = [rng.choice(["1", "0x1F", "0b101", "A", "b_2", "7"]) for _ in range(rng.randint(1, 4))]
        expr = toks[0] + "".join(rng.choice(["+", "-", " + ", "- "]) + t for t in toks[1:])
        assert mp.value(expr, syms) == pc.value(expr, syms), expr
