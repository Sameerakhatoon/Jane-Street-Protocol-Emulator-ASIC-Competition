"""State machines: environment separations, lowering, analysis, fixes, assists."""
from fractions import Fraction as Fr

import pytest

from tools.tsec import assists, fix, lir
from tools.tsec import ast as A
from tools.tsec.cycles import FetchModel
from tools.tsec.diag import Errors
from tools.tsec.fsm import Fsm, layout
from tools.tsec.fsmsched import Scheduler
from tools.tsec.semantics import min_separation, stable_after

from helpers import FCLK, codes, front

with open("specs/spi_target.tse") as fh:
    SPI = fh.read()
with open("specs/i2c_target.tse") as fh:
    I2C = fh.read()


def ev(kind, name):
    return A.Event(kind, A.Ident(name))


@pytest.mark.parametrize("args,check,residue", [
    ((16, 0x8005, 0xFFFF, 1, 1, 0xFFFF), 0xB4C8, 0xB001),
    ((5, 0x05, 0x1F, 1, 1, 0x1F), 0x19, 0x06),
    ((15, 0x4599, 0, 0, 0, 0), 0x059E, 0),
    ((32, 0x04C11DB7, 0xFFFFFFFF, 1, 1, 0xFFFFFFFF), 0xCBF43926, 0xDEBB20E3)])
def test_crc_catalogue(args, check, residue):
    w = args[0]
    assert assists.crc(b"123456789", *args) == check
    raw = assists.residue(*args)
    assert (assists.reflect(raw, w) if args[4] else raw) == residue


def test_crc_hint_names_the_missing_flags():
    src = "protocol p { assist c : crc { width = 16; poly = 0x8005; init = 0xFFFF; " \
          "xorout = 0xFFFF; check = 0xB4C8; } }"
    with pytest.raises(Errors) as e:
        front(src)
    assert codes(e) == ["E231"] and "refin and refout missing" in e.value.errs[0].msg


def test_separations_from_the_i2c_datasheet():
    sym = front(I2C)
    role = sym.roles[0]
    t, rules = min_separation(sym, role, ev("rise", "scl"), ev("rise", "scl"), FCLK)
    assert t == 114 and [r.name.text for r in rules] == ["t_high", "t_low"]        # 0.6 + 1.3 us
    t, rules = min_separation(sym, role, ev("fall", "scl"), ev("named", "stop"), FCLK)
    assert t == 114 and [r.name.text for r in rules] == ["t_low", "t_su_sto"]      # through rise(scl)
    t, _ = min_separation(sym, role, ev("named", "start"), ev("fall", "scl"), FCLK)
    assert t == 36
    assert min_separation(sym, role, ev("named", "start"), ev("named", "stop"), FCLK) == (0, [])


def test_stability_after_start_covers_both_scl_levels():
    sym = front(I2C)
    # scl == 1 (a start): t_hd_sta 0.6 us; scl == 0: t_su_dat 100 ns. The least is 6 ticks.
    assert stable_after(sym, sym.roles[0], ev("fall", "sda"), "scl", FCLK) == 6
    assert stable_after(sym, sym.roles[0], ev("fall", "scl"), "sda", FCLK) == 0


def test_spi_lowering_shape():
    sym = front(SPI)
    prog, labels = layout(Fsm(sym, sym.roles[0], FCLK, "rolled").lower())
    ops = [(i.op, i.a.get("cond") or i.a.get("dst") or i.a.get("src")) for i in prog]
    assert ("PULL", None) in ops and ("JMP", "YDEC") in ops and ("JMP", "TOUT") in ops
    waits = [i for i in prog if i.op == "WAIT"]
    assert sum(1 for w in waits if w.a.get("alt")) == 3        # the level before the loop and two edges
    loop = labels["loop3"]
    assert loop % 2 == 0                                         # the loop head starts a word
    assert any("OB-WIN shift miso" in line for line in lir.listing(prog))


def test_spi_first_bit_numbers_and_fix():
    sym = front(SPI)
    att, tried = Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), (0, 25)).compile()
    assert att is None
    first = next(v for v in tried[-1].violations if v.span.line == 46)
    fields = dict(first.fields)
    assert fields["late by"] == "63.3 ns"
    assert fields["fix"] == "t_css >= 164 ns (139 ns with ideal pads)"


def test_spi_relaxed_is_accepted():
    sym = front(SPI, t_css="170e-9", f_sck="3.8e6")
    att, _ = Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), (0, 25)).compile()
    assert att is not None and att.mode == "rolled" and len(att.prog) == 16


def test_fix_solves_a_frequency_and_rounds_safely():
    sym = front(SPI)
    hi = next(s for s in sym.spec.of(A.Role)[0].states if s.name.text == "bits").edges[0] \
        .body[0].body[0].timing.window.hi                       # half - t_mo
    out = dict(fix.solve(sym, hi, Fr(110, 10**9), at_least=True))
    assert out["f_sck"] == "f_sck <= 3.84 MHz" and out["t_mo"] == "t_mo <= 14.9 ns"
    assert fix.safe_round(Fr(38888, 10), up=False) == Fr(3880, 1)


def test_w220_disjoint_guards_are_quiet():
    src = """protocol p { wire clk : in; wire d : in;
      role r : target uses clk, d {
        state s { on fall(clk) when d == 1 -> t; on fall(clk) when d == 0 -> t; }
        state t { on rise(clk) -> s; } } }"""
    assert front_warnings(src) == []


def front_warnings(src):
    from tools.tsec.check import check
    from tools.tsec.parser import parse
    from tools.tsec.resolve import resolve
    return [w.code for w in check(resolve(parse(src)))]
