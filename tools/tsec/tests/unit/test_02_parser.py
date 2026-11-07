# SPDX-License-Identifier: Apache-2.0
import pytest

from tools.tsec import ast as A
from tools.tsec import printer
from tools.tsec.diag import Errors
from tools.tsec.parser import parse

from helpers import UART, codes


def test_uart_shape():
    spec = parse(UART)
    assert spec.name.text == "uart"
    tx, rx = spec.of(A.Role)
    assert (tx.kind, [u.text for u in tx.uses]) == ("controller", ["tx"])
    seq = tx.states[1].edges[0]
    assert isinstance(seq, A.SeqEdge) and seq.target.text == "idle"
    drive, shift, stop = seq.body
    assert isinstance(drive, A.Drive) and isinstance(shift, A.Shift) and shift.out
    assert printer.expr(shift.each) == "1 bit"
    sample = rx.states[1].edges[0].body[0]
    assert sample.timing.window.after.kind == "fall"


def test_shift_direction_by_lookahead():
    src = """protocol p { wire w : in; role r : target uses w { var v : bits[8];
      state s { seq { shift w -> v msb 8 each 1 us; shift v lsb 8 -> w each 1 us; } -> s; } } }"""
    a, b = parse(src).of(A.Role)[0].states[0].edges[0].body
    assert (a.out, a.order, b.out, b.order) == (False, "msb", True, "lsb")


def test_unit_attaches_after_a_number_but_not_to_keywords():
    src = ("protocol p { wire w : out; role r : controller uses w { var v : bits[1]; "
           "state s { seq { drive w = 0 for 1 us; } -> s; } } }")
    drive = parse(src).of(A.Role)[0].states[0].edges[0].body[0]
    assert drive.value.unit is None and drive.timing.for_.unit.text == "us"


def test_precedence():
    e = parse("protocol p { param x : count = 1 + 2 * 3 - 4 / 2; }").items[0].value
    assert printer.expr(e) == "1 + 2 * 3 - 4 / 2"
    assert e.op == "-" and e.left.op == "+" and e.left.right.op == "*"


def test_parentheses_survive_printing():
    spec = parse("protocol p { param x : count = (1 + 2) * 3 - (4 - 2); }")
    assert printer.expr(spec.items[0].value) == "(1 + 2) * 3 - (4 - 2)"


def test_round_trip_ignores_spans():
    spec = parse(UART)
    assert parse(printer.spec(spec)) == spec


def test_recovery_reports_every_error():
    src = """protocol p {
  param a : time 1 us;
  wire w : out bogus;
  role r : controller uses w {
    var v : bits[8];
    state s { seq { frobnicate w; drive w = 0; } -> s; }
  }
}"""
    with pytest.raises(Errors) as e:
        parse(src)
    assert codes(e) == ["E010", "E016", "E020"]


def test_error_cap():
    src = "protocol p {\n" + "  junk;\n" * 40 + "}"
    with pytest.raises(Errors) as e:
        parse(src)
    assert len(e.value.errs) == 20


def test_respond_and_guards():
    src = """protocol p { param a7 : bits[7] = 0x50; wire scl : in; wire sda : inout open_drain;
      role t : target uses scl, sda { var sh : bits[8];
        state d { respond ack : on fall(scl) when sh[7:1] == a7 and not sda == 1
                  { drive sda = 0; } within [300 ns, 900 ns] -> d; } } }"""
    edge = parse(src).of(A.Role)[0].states[0].edges[0]
    assert isinstance(edge, A.RespondEdge) and edge.name.text == "ack"
    assert printer.expr(edge.guard) == "sh[7:1] == a7 and not sda == 1"


def test_dump_shows_spans():
    out = A.dump(parse(UART))
    assert "Spec name=uart @17:10" in out
    assert "Drive wire=tx value=(0) for=(1 bit) tol=(tol) @30:9" in out
