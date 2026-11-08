# SPDX-License-Identifier: Apache-2.0
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
    assert ("PULL", None) in ops and ("JMP", "YDEC") in ops and ("JMP", "PIN") in ops
    waits = [i for i in prog if i.op == "WAIT"]
    assert sum(1 for w in waits if w.a.get("alt")) == 3        # the level before the loop and two edges
    loop = next(i for name, i in labels.items() if name.startswith("loop"))
    assert loop % 2 == 0                                         # the loop head starts a word
    assert any("OB-WIN shift miso" in line for line in lir.listing(prog))


def test_spi_first_bit_numbers_and_fix():
    sym = front(SPI)
    att, tried = Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), (0, 25)).compile()
    assert att is None                  # rejected for the byte gap at 4 MHz (test_14)
    first = next(v for t in tried for v in t.violations if v.span.line == 55)  # the sequencer alone
    fields = dict(first.fields)
    assert fields["late by"] == "13.3 ns"          # the reaction is buffered on every path
    assert fields["fix"] == "t_css >= 114 ns,\nor pads characterised at or below 11.7 ns"


def test_spi_relaxed_is_accepted():
    sym = front(SPI, t_css="170e-9", f_sck="1.875e6")
    att, _ = Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), (0, 25)).compile()
    # 21: the jump back to the byte loop sets its count itself and skips the wait for SCK low
    assert att is not None and att.mode == "rolled" and len(att.prog) == 21


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


SET_THEN_SHIFT = """protocol p { wire clk : in; wire dat : in;
  role r : target uses clk, dat {
    var d : bits[16];
    state s {
      seq { set d = 1; repeat 8 { await rise(clk); shift dat -> d msb 1; } push host d; } -> s;
    } } }"""


@pytest.mark.parametrize("mode", ["rolled", "unrolled"])
def test_shift_in_after_set_keeps_the_set_value(mode):
    # d = 1 then eight bits in: 0x1nn. IN shifts ISR, so the 1 (in OSR after the set) must be
    # copied into ISR once, before the loop, not on every pass (that would drop earlier bits)
    sym = front(SET_THEN_SHIFT)
    prog, labels = layout(Fsm(sym, sym.roles[0], FCLK, mode).lower())
    copies = [k for k, i in enumerate(prog) if i.op == "IN" and i.a.get("src") == "OSR"]
    samples = [k for k, i in enumerate(prog) if i.op == "IN" and i.a.get("src") == "PINS"]
    assert len(copies) == 1 and copies[0] < samples[0]
    assert len(samples) == (1 if mode == "rolled" else 8)
    if mode == "rolled":
        loop = next(k for name, k in labels.items() if name.startswith("loop"))
        assert copies[0] < loop


def test_shift_in_without_set_adds_nothing():
    sym = front(SET_THEN_SHIFT.replace("set d = 1; ", ""))
    prog, _ = layout(Fsm(sym, sym.roles[0], FCLK, "rolled").lower())
    assert not any(i.op == "IN" and i.a.get("src") == "OSR" for i in prog)


with open("specs/i2c_target_fm.tse") as fh:
    I2C_FM = fh.read()


def test_i2c_target_with_restart_is_accepted():
    sym = front(I2C_FM)
    att, _ = Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), (0, 25)).compile()
    assert att is not None and att.mode == "rolled"


def test_after_an_abort_separations_count_from_the_abort_event():
    # Every abort into 'addr' is on cond (a START or STOP), so after it the next cond is bounded
    # by the cond -> cond rule. Without that rule the re-arm check must name cond as the event
    # before, not an older SCL edge or START that the abort made stale.
    sym = front(I2C_FM.replace("    rule t_buf_c  : cond      -> cond      >= 1.3 us;\n", ""))
    att, tried = Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), (0, 25)).compile()
    assert att is None
    rearm = [dict(v.fields) for t in tried for v in t.violations if v.code == "R314"]
    assert rearm and all(f["required"] == "armed before the earliest cond after cond" for f in rearm)


def front_warnings(src):
    from tools.tsec.check import check
    from tools.tsec.parser import parse
    from tools.tsec.resolve import resolve
    return [w.code for w in check(resolve(parse(src)))]
