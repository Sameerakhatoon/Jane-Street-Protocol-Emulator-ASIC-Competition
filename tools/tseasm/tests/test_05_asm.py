"""Assembler: the UART TX example, directives, and errors with line numbers."""
from pathlib import Path

import pytest

from tools.tseasm import AsmError, assemble, disasm, hex32, isa

HERE = Path(__file__).parent


def words(text):
    return assemble(text).words


# 1: the UART TX example assembles to the words in uart_tx.hex
def test_01_uart_example():
    prog = assemble((HERE / "uart_tx.s").read_text())
    want = [int(x, 16) for x in (HERE / "uart_tx.hex").read_text().split()]
    assert prog.words == want
    assert hex32(prog.words).split() == ["41FD4011", "4207C300", "E8064810", "0A068806", "00024011"]
    assert prog.labels == {"frame": 2, "bit": 6}
    assert (prog.name, prog.window, prog.start, prog.config) == ("uart_tx", 0, 0, [("LANE_CFG", 0x60)])


# 2: a slower hand-written transmitter assembles and disassembles back
def test_02_slow_transmitter():
    src = """
.program uart_tx
.window 0
.start 0
.side_set 1 opt
.config DPLL_PERIOD 1042
idle:   PULL block
        SET X, 7
        SET PINS, 0, mask=0b0001
        DLY 64
bit:    OUT PINS, 1
        DLY 62
        JMP XDEC, bit
        SET PINS, 1, mask=0b0001 side 1
        DLY 62
        JMP ALWAYS, idle
"""
    prog = assemble(src)
    assert len(prog.words) == 10 and prog.labels["bit"] == 4
    assert words(disasm(prog.words)) == prog.words


# 3: operand spellings that come from the table
@pytest.mark.parametrize("text,word", [
    ("WAIT 1, PIN, 5, alt", 0x24A8),
    ("WAIT 1, PIN, 4+1, alt", 0x24A8),
    ("WAIT 0, FLAG, ONE, to [7]", isa.encode("WAIT", pol=0, src="FLAG", idx=7, to=1, delay=7)),
    ("WAIT 1, EV, TIMER", isa.encode("WAIT", pol=1, src="EV", idx=0)),
    ("WAIT 1, CHIP, CF2", isa.encode("WAIT", pol=1, src="CHIP", idx=2)),
    ("LDW X", 0xC780),
    ("STW 127", isa.encode("STW", addr=127)),
    ("TMR cclr, gsel=1 [2]", isa.encode("TMR", cclr=1, gwe=1, gsel=1, delay=2)),
    ("TMR", isa.encode("TMR")),
    ("PUSH noblock", isa.encode("PUSH")),
    ("CSRW 0x18", isa.encode("CSRW", addr=0x18)),
    ("csrr status", isa.encode("CSRR", addr=0x23)),
    ("EMIT 5 [2]", 0xE62A),
    ("OUT PINS, 1 side 1 [6]", 0x8806),
    ("SET PINDIRS, 0b1101", 0x41FD),
    ("SIG 0b0011, 0b0001", isa.encode("SIG", mask=3, val=1)),
    ("MOV PC, NOTX", isa.encode("MOV", dst="PC", src="NOTX")),
])
def test_03_spellings(text, word):
    assert words(text) == [word]


# 4: directives: .define, .align (pads with DLY 0), .word, .start with a label
def test_04_directives():
    prog = assemble(".define N 6\n.start top\nSET X, N\n.align 2\ntop: .word 0x1234\nJMP ALWAYS, top\n")
    assert prog.words == [isa.encode("SET", dst="X", imm=6), isa.encode("DLY"), 0x1234,
                          isa.encode("JMP", target=2)]
    assert prog.start == 2


# 5: errors carry the line number
@pytest.mark.parametrize("text,what", [
    ("NOP", "unknown instruction"),
    ("WAIT 1, PIN, 0 [8]", "outside 0 to 7"),
    ("JMP ALWAYS, nowhere", "unknown symbol"),
    ("JMP XDEC", "takes 2 operand"),
    ("CSRW STATUS", "not a writable"),
    ("CSRW 0x05", "not a writable"),
    ("CSRR LANE_CFG", "cannot read"),
    ("EMIT 0x3E", "reserved"),
    ("OUT PINS, 33", "outside 1 to 32"),
    ("OUT 6, 1", "reserved outdst"),
    ("SET X, 1, mask=1", "unknown key"),
    ("SET PINS, 16", "4 bits"),
    ("DLY 1 [2]", "no \\[delay\\]"),
    ("a: DLY 1\na: DLY 2", "defined twice"),
    (".config RULE0_RESP_POS 0x00FF0000", "outside"),
    (".config STATUS 0", "not a writable"),
    (".bogus", "unknown directive"),
    ("JMP ALWAYS, 0 side 4", "side-set"),
])
def test_05_errors(text, what):
    with pytest.raises(AsmError, match=what) as e:
        assemble("; first line\n" + text)
    assert e.value.lineno >= 2


# 6: a program longer than one window is refused
def test_06_too_long():
    with pytest.raises(AsmError, match="longer than 256"):
        assemble("DLY 0\n" * 257)
