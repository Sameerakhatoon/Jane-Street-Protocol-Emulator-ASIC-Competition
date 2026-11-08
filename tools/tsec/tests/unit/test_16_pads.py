# SPDX-License-Identifier: Apache-2.0
"""Pad delays per direction (--pad-in-*, --pad-out-*): without them every result is the one of
the single range; with them a sample after an input edge and two input events take the input
range, two of our own drives the output skew, and a path through both directions one pad in the
hull of the two ranges (pads.py)."""
from fractions import Fraction as Fr
from types import SimpleNamespace as NS

import pytest

from tools.tsec import cli, proofgen
from tools.tsec.cycles import FetchModel
from tools.tsec.fsmsched import Scheduler
from tools.tsec.pads import Pads

from helpers import FCLK, front

with open("specs/spi_target.tse") as fh:
    SPI = fh.read()
with open("specs/jtag_probe.tse") as fh:
    JTAG = fh.read()

SPLIT = ["--pad-in-min", "0", "--pad-in-max", "4.5", "--pad-out-min", "0", "--pad-out-max", "21",
         "--pad-out-skew", "3.5"]


def flags(*argv):
    return cli.pads_of(cli.arg_parser().parse_args(["build", "x.tse", *argv]))


def compile_role(src, pads, **over):
    sym = front(src, **over)
    return Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), pads).compile()


def r314(tried):
    return [v for t in tried for v in t.violations if v.code == "R314"]


# ---------------------------------------------------------------- the flags
def test_without_the_new_flags_every_direction_is_the_single_range():
    for argv, rng in (([], (0, 25, 25)), (["--pad-skew", "0"], (0, 25, 0)),
                      (["--pad-min", "0", "--pad-max", "21", "--pad-skew", "3.5"], (0, 21, 3.5))):
        p = flags(*argv)
        assert p.inp == p.out == rng and p.cross == rng[2] and not p.split
        assert p.hull() == rng[:2] and p.window("in") == p.window("out") == rng[:2]


def test_a_direction_given_equal_to_the_single_range_is_not_a_split():
    p = flags("--pad-in-min", "0", "--pad-in-max", "25", "--pad-out-skew", "25")
    assert not p.split and p.inp == p.out == (0, 25, 25)


def test_split_ranges_and_their_skews():
    p = flags(*SPLIT)
    assert p.split and p.inp == (0, 4.5, 4.5) and p.out == (0, 21, 3.5)
    assert p.window("in") == (0, 4.5) and p.window("out") == p.hull() == (0, 21)
    # an input pad against an output pad: anything the two ranges allow
    assert p.skew("in", "in") == 4.5 and p.skew("out", "out") == 3.5 and p.skew("in", "out") == 21
    # --pad-skew bounds any two pads, so it caps a direction's default skew and the cross skew
    p = flags(*SPLIT, "--pad-skew", "3.5")
    assert p.inp == (0, 4.5, 3.5) and p.cross == 3.5
    assert p.skew_flag("in", "in") == "--pad-in-skew" and p.skew_flag("out", "in") == "--pad-skew"


def test_one_direction_given_keeps_the_other_at_the_single_range():
    p = flags("--pad-max", "21", "--pad-out-skew", "3.5")
    assert p.inp == (0, 21, 21) and p.out == (0, 21, 3.5) and p.cross == 21


def test_bad_split_is_a_usage_error(capsys):
    assert "least <= largest" in flags("--pad-in-min", "6", "--pad-in-max", "5")
    assert cli.main(["build", "specs/uart.tse", "--pad-out-skew", "-1"]) == 1
    assert "--pad-out-*" in capsys.readouterr().err


def test_old_tuples_are_one_range_for_every_pad():
    assert Pads.of((0, 25)).out == (0, 25, 25) and Pads.of((0, 25, 0)).cross == 0
    rng = (Fr(3, 50), Fr(3, 25))              # 1 and 2 ns at 60 MHz
    assert Pads.of((1, 2)).ticks_by_kind(FCLK) == {"in": rng, "out": rng}


# ---------------------------------------------------------------- which range an analysis takes
def test_window_kinds_take_their_range():
    sym = front(SPI, t_css="142e-9", f_sck="1.875e6")
    s = Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), flags(*SPLIT))
    st = NS(dmin=10, dmax=12)
    assert s.achieved(NS(kind="in"), st) == (10, 13 + FCLK * Fr(4.5) / 10**9)
    assert s.achieved(NS(kind="out"), st) == (13, 16 + FCLK * 21 / 10**9)
    # a sample after our own output edge crosses an output and an input pad: the hull
    assert s.sep_bounds(NS(kind="in", ticks=5, hi=None))[0] == 8 + FCLK * 21 / 10**9
    # a hold from our drive: to a drive on another pin the output skew, to a sample the cross skew
    assert s.sep_bounds(NS(kind="out", ticks=5, hi=None, skew=True, drive=True))[0] == \
        5 + FCLK * Fr(3.5) / 10**9
    assert s.sep_bounds(NS(kind="out", ticks=5, hi=None, skew=True, drive=False))[0] == 5 + FCLK * 21 / 10**9
    assert s.sep_bounds(NS(kind="out", ticks=5, hi=None, skew=False, drive=False))[0] == 5


def test_proof_bounds_take_the_range_of_their_kind():
    with open("specs/i2c_target_fm.tse") as fh:
        sym = front(fh.read())
    att, _ = Scheduler(sym, sym.roles[0], 0, FCLK, FetchModel(), (0, 25)).compile()
    obs = proofgen.fsm_obs(sym, sym.roles[0], 0, att, FetchModel(), FCLK)
    win = flags(*SPLIT).ticks_by_kind(FCLK)
    _, rows = proofgen.harness(sym.roles[0], 0, obs, {"LANE_CFG": 0}, [0], [], win)
    assert rows and all((lo, hi) == proofgen.window_bounds(ob.lo, ob.hi, *win[ob.kind], ob.kind)
                        for ob, lo, hi in rows)
    # one range for both kinds, as the tests and older callers pass it
    _, old = proofgen.harness(sym.roles[0], 0, obs, {"LANE_CFG": 0}, [0], [], win["out"])
    assert old == [(ob, *proofgen.window_bounds(ob.lo, ob.hi, *win["out"], ob.kind))
                   for ob in obs]


# ---------------------------------------------------------------- accepted and rejected
def test_cs_setup_takes_the_input_skew():
    # 117 ns builds only with no skew between the CS and SCK pads (test_14). Outputs alone do not
    # help: CS and SCK are inputs. An input skew of 4.5 ns needs 122 ns at 2 MHz
    assert compile_role(SPI, flags("--pad-out-skew", "0"), t_css="117e-9", f_sck="1.875e6")[0] is None
    assert compile_role(SPI, flags("--pad-in-skew", "0"), t_css="117e-9", f_sck="1.875e6")[0] is not None
    att, tried = compile_role(SPI, flags(*SPLIT), t_css="121e-9", f_sck="2e6")
    assert att is None and r314(tried)
    assert compile_role(SPI, flags(*SPLIT), t_css="122e-9", f_sck="2e6")[0] is not None
    assert compile_role(SPI, flags(*SPLIT, "--pad-in-skew", "3.5"), t_css="121e-9", f_sck="2e6")[0] \
        is not None


def test_rule_fix_adds_the_input_skew_back():
    att, tried = compile_role(SPI, flags(*SPLIT), t_css="117e-9", f_sck="1.875e6")
    # 7 cycles at the lane plus the 4.5 ns input skew
    assert att is None and any(dict(v.fields)["fix"].startswith("t_css >= 122 ns") for v in r314(tried))
    assert compile_role(SPI, flags(*SPLIT), t_css="122e-9", f_sck="1.875e6")[0] is not None


def test_a_drive_and_a_sample_take_the_cross_skew():
    # The probe samples TDO between its own TCK drives. A zero output skew alone leaves an input
    # pad against an output pad at the whole default range; with --pad-skew 0 it builds.
    att, tried = compile_role(JTAG, flags("--pad-out-skew", "0"))
    assert att is None
    bad = [v for t in tried for v in t.violations if v.code == "R312"]
    assert bad and all("(--pad-skew)" in dict(v.fields)["fix"] for v in bad
                       if "pads characterised" in dict(v.fields)["fix"])
    assert compile_role(JTAG, flags("--pad-out-skew", "0", "--pad-skew", "0"))[0] is not None


def outcome(src, pads):
    att, tried = compile_role(src, pads)
    return att is None, [(t.mode, [i.a for i in t.prog], [(v.code, v.msg, v.fields) for v in t.violations])
                         for t in tried]


@pytest.mark.parametrize("argv", [[], ["--pad-skew", "0"], ["--pad-in-max", "25", "--pad-out-skew", "25"]])
def test_default_flags_give_the_old_tuple_result(argv):
    p = flags(*argv)
    assert outcome(JTAG, p) == outcome(JTAG, (0, 25, p.inp[2]))


def test_cli_report_is_byte_identical_without_a_split(tmp_path, capsys):
    outs = []
    for k, argv in enumerate([["--pad-min", "0", "--pad-max", "21", "--pad-skew", "3.5"],
                              ["--pad-min", "0", "--pad-max", "21", "--pad-skew", "3.5",
                               "--pad-in-max", "21", "--pad-out-min", "0"]]):
        assert cli.main(["build", "specs/jtag_probe.tse", *argv, "-o", str(tmp_path / str(k))]) == 0
        outs.append((capsys.readouterr().out,
                     *((tmp_path / str(k) / f"jtag_probe.{e}").read_text() for e in ("img", "report.txt"))))
    assert outs[0] == outs[1] and "pads 0 to 21 ns" in outs[0][0]


def test_split_report_names_both_ranges(capsys):
    cli.main(["build", "specs/spi_target_mode0_css142.tse", *SPLIT])
    assert "pads in 0 to 4.5 ns, out 0 to 21 ns" in capsys.readouterr().out
