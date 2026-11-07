# SPDX-License-Identifier: Apache-2.0
from fractions import Fraction as Fr

import pytest

from tools.tsec.diag import Errors
from tools.tsec.parser import parse
from tools.tsec.resolve import resolve

from helpers import UART, codes


def test_uart_symbols():
    sym = resolve(parse(UART))
    ty, baud = sym.params["baud"]
    assert (ty, baud.v, baud.dim) == ("freq", 115200, -1)
    assert sym.units["bit"].v == Fr(1, 115200) and sym.units["bit"].dim == 1
    assert sym.params["tol"][1].v == Fr(1, 50)
    assert set(sym.wires) == {"tx", "rx"} and len(sym.roles) == 2


def test_override_keeps_exact_value_and_dimension():
    sym = resolve(parse(UART), {"baud": "921600"})
    assert sym.params["baud"][1].v == 921600
    assert sym.units["bit"].v == Fr(1, 921600)


@pytest.mark.parametrize("expr, value, dim", [
    ("4.7 us", Fr(47, 10**7), 1), ("400 kHz", 400_000, -1), ("2 %", Fr(1, 50), 0),
    ("50 ppm", Fr(50, 10**6), 0), ("1 / (2 * 1 MHz)", Fr(1, 2 * 10**6), 1),
    ("0xF0 | 0b0101", 0xF5, 0), ("1 << 4", 16, 0), ("-(1 us - 3 us)", Fr(2, 10**6), 1),
])
def test_constant_evaluation(expr, value, dim):
    sym = resolve(parse(f"protocol p {{ unit u = {expr}; }}"))
    q = sym.units["u"]
    assert (q.v, q.dim) == (value, dim)


@pytest.mark.parametrize("item, code", [
    ("param t : time = 1 us + 1 Hz;", "E102"),
    ("param t : time = 1 us - 1 Hz;", "E102"),
    ("param t : speed = 1;", "E103"),
    ("param t : bits = 1;", "E103"),
    ("param t : freq = 1 us;", "E104"),
    ("param t : ratio = 1 / 0;", "E105"),
    ("param t : time = 3 lightyears;", "E101"),
    ("param t : time = later;", "E101"),
    ("param t : count = 1.5 & 1;", "E110"),
    ("param t : count = 1 < 2;", "E110"),
    ("param us : time = 1 us;", "E215"),
])
def test_resolve_errors(item, code):
    with pytest.raises(Errors) as e:
        resolve(parse(f"protocol p {{ {item} }}"))
    assert codes(e) == [code]


def test_no_forward_references():
    with pytest.raises(Errors) as e:
        resolve(parse("protocol p { param a : time = 2 b; unit b = 1 us; }"))
    assert codes(e) == ["E101"]


def test_override_errors():
    src = "protocol p { const c : time = 1 us; param f : freq = 1 Hz; }"
    with pytest.raises(Errors) as e:
        resolve(parse(src), {"c": "2", "f": "x", "nope": "1"})
    assert sorted(codes(e)) == ["E106", "E107", "E108"]
