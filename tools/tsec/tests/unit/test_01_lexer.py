# SPDX-License-Identifier: Apache-2.0
from fractions import Fraction as Fr

import pytest

from tools.tsec.diag import TseError
from tools.tsec.lexer import lex


def kinds(src):
    return [(t.kind, t.text) for t in lex(src)]


def test_names_numbers_symbols_and_positions():
    toks = lex("param baud : freq = 115200 Hz;\n  x -> y")
    assert [(t.kind, t.text) for t in toks[:4]] == [("name", "param"), ("name", "baud"),
                                                   ("sym", ":"), ("name", "freq")]
    num = toks[5]
    assert (num.kind, num.value, num.span.line, num.span.col, num.span.width) == \
        ("num", 115200, 1, 21, 6)
    arrow = toks[-3]
    assert (arrow.text, arrow.span.line, arrow.span.col) == ("->", 2, 5)
    assert toks[-1].kind == "eof"


def test_number_forms_are_exact():
    vals = [t.value for t in lex("0x8005 0b1010_0101 1_000 4.7 0.4") if t.kind == "num"]
    assert vals == [0x8005, 0b10100101, 1000, Fr(47, 10), Fr(2, 5)]


def test_range_dots_are_not_a_fraction():
    assert kinds("1..8")[:3] == [("num", "1"), ("sym", ".."), ("num", "8")]


def test_two_character_symbols_win():
    assert [t.text for t in lex("-> == != <= >= << >> ..")][:-1] == \
        ["->", "==", "!=", "<=", ">=", "<<", ">>", ".."]


def test_comments_and_whitespace_vanish():
    assert kinds("// all of this\n  \t x // more\n") == [("name", "x"), ("eof", "")]


def test_bad_character():
    with pytest.raises(TseError) as e:
        lex("a $ b")
    assert (e.value.code, e.value.span.col) == ("E001", 3)


@pytest.mark.parametrize("src", ["0x", "0b2", "0x1G"])
def test_malformed_numbers(src):
    with pytest.raises(TseError) as e:
        lex(src)
    assert e.value.code == "E002"
