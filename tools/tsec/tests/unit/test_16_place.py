# SPDX-License-Identifier: Apache-2.0
"""Own-instant proof harnesses (proofgen.fsm_place, frame_place): paced chains, distances after
our own edge and paced-frame placements, each checked on the lane as the analysis counts it."""
from fractions import Fraction as Fr

from tools.tsec import proofgen
from tools.tsec.cycles import FetchModel as CyclesFetch
from tools.tsec.fsmsched import Scheduler
from tools.tsec.schedule import compile_role
from tools.tsec.timing import FetchModel

from helpers import FCLK, front


def spec(name):
    with open(f"specs/{name}.tse") as fh:
        return front(fh.read())


def fsm(name, pads=(0, 25, 25)):
    sym = spec(name)
    att, _ = Scheduler(sym, sym.roles[0], 0, FCLK, CyclesFetch(), pads).compile()
    assert att is not None
    ticks = tuple(Fr(p) * FCLK / 10**9 for p in pads)
    return sym, att, proofgen.fsm_place(att, 0, CyclesFetch(), 50, ticks[:2], ticks[2])


def test_chain_windows_hold_the_analysis_claims():
    # SPI controller, pads without skew (it is rejected with the default skew): every entry the
    # analysis recorded lies inside the window the harness asserts, and entry 0 checks nothing
    _, _, pl = fsm("spi_controller_mode0", (0, 25, 0))
    assert len(pl.groups) == 11 and not pl.skipped
    rows = [r for c in pl.chains for r in c]
    assert all(g is None for _, _, g, _ in (c[0] for c in pl.chains))
    claimed = [(lo, hi, c) for lo, hi, g, c in rows if g is not None]
    assert claimed and all(c is not None and lo <= c[0] <= c[1] <= hi for lo, hi, c in claimed)


def test_chain_harness_counts_entries_and_plants_one_bug_per_obligation():
    sym, _, pl = fsm("spi_controller_mode0", (0, 25, 0))
    top = proofgen.place_harness(sym.roles[0], 0, pl, {"LANE_CFG": 0}, [0], [])
    assert "assert (!order_bad && lem_ok);" in top and "dd = pt;" in top and "n_pt = " in top
    for g in range(len(pl.groups)):
        assert f"LATE == {g} ||" in top
        assert f"if (ONLY < 0 || ONLY == {g}) assert (!bad[{g}] && lem_ok);" in top
    # a wait for the host ends the chain, as in the analysis
    assert pl.resets and all("n_ch = '0;" in top for _ in pl.resets)
    sby = proofgen.place_sby("ctl", len(pl.groups), True)
    assert sby.index("flatten") < sby.index("connect -set p_dly dut.u_seq.dly") < sby.index("memory_map")
    assert "connect -set" not in proofgen.place_sby("ctl", len(pl.groups))
    assert "prv: abc pdr" in sby and "late10 prv" in sby and "top_ctl_place.sv" in sby
    assert "rprove ric" in sby and "ric: aiger rIC3" in sby and "rlate10 ric" in sby


def test_distances_after_our_own_edge():
    # I2C controller: no paced chain, 30 least distances; one between different wires counts the
    # pad skew against the distance (fsmsched.sep_bounds), one on the same wire does not
    _, att, pl = fsm("i2c_controller_fm")
    assert not pl.chains and len(pl.seps) == len(pl.groups) == 30
    skew = Fr(25) * FCLK / 10**9
    for (r, p, lo, hi, g, claim), ins in zip(pl.seps, [i for i in att.prog if i.sep is not None]):
        s = ins.sep
        assert att.prog[r] is s.ref and att.prog[p] is ins and hi is None
        assert lo == -(-(s.ticks + (skew if s.skew else 0)) // 1)
        assert claim is not None and claim[0] >= lo           # the analysis' least distance holds
    top = proofgen.place_harness(spec("i2c_controller_fm").roles[0], 0, pl, {"LANE_CFG": 0}, [0], [])
    assert "sv54" in top and "sr54 <= " in top and "&& sv54) begin" in top


def test_a_role_with_an_abort_has_no_place_harness():
    _, _, pl = fsm("spi_flash_target")
    assert not pl.groups and pl.skipped and "abort" in pl.skipped[0]


def test_paced_frame_visits_in_issue_order():
    # UART transmitter at 115200 baud: start bit, 8 data bits, stop bit, 520.8 ticks apart,
    # each within its tolerance less 50 ppm of drift; all outputs, so no output offset
    sym = spec("uart_tx")
    res = compile_role(sym, sym.roles[0], 0, FCLK, FetchModel(), 50)
    low = res.chosen.low
    pl = proofgen.frame_place(low, res.chosen.labels, 0, CyclesFetch(), 50)
    rows = pl.chains[0]
    assert len(rows) == 10 and [t[2] for t in rows] == [None] + [0] * 8 + [1]
    assert [code for _, _, code in pl.groups] == ["OB-EDGE", "OB-EDGE"]
    bit = Fr(FCLK, 115200)
    for k, (lo, hi, _, _) in enumerate(rows[1:], 1):
        assert lo <= k * bit <= hi and hi - lo <= 21
    assert pl.resets == {res.chosen.labels["idle"]}
    top = proofgen.place_harness(sym.roles[0], 0, pl, {"LANE_CFG": 0}, [0], [])
    assert "8'd9: begin" in top and "default: order_bad = 1'b1;" in top


def test_lemmas_from_a_simulation_are_asserted_not_assumed(tmp_path):
    # place_lemmas.py turns a LEMLOG run into q ranges per (chain, entry, pc) inside lem_ok, which
    # every task asserts with its obligation; a wait that ends a chain gets none (the host is free)
    import importlib.util
    spec_ = importlib.util.spec_from_file_location("place_lemmas", "tools/tsec/proofs/sim/place_lemmas.py")
    lem = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(lem)
    sym, _, pl = fsm("spi_controller_mode0", (0, 25, 0))
    top = tmp_path / "top.sv"
    top.write_text(proofgen.place_harness(sym.roles[0], 0, pl, {"LANE_CFG": 0}, [0], []))
    end = min(pl.resets)
    log = tmp_path / "lem.log"
    log.write_text(f"lem 3 5 70 40 70\nlem 3 5 70 44 70\nlem 3 6 71 50 70\nlem 1 9 {end} 7 {end}\n"
                   "place g0 k1 0\n")
    lem.main(str(top), str(log), 1)
    text = top.read_text()
    assert "assign lem_ok = 1'b1;" not in text and "always_comb begin\n    lem_ok = 1'b1;" in text
    assert f"'d{(3 << 16) | (5 << 8) | 70}: lem_ok = q >= " in text and "'d39 && q <= " in text
    assert f"'d{(1 << 16) | (9 << 8) | end}:" not in text
    assert "assume" not in text.split("// lemmas from a simulation")[1].split("endmodule")[0]


def load(name):
    import importlib.util
    spec_ = importlib.util.spec_from_file_location(name, f"tools/tsec/proofs/sim/{name}.py")
    mod = importlib.util.module_from_spec(spec_)
    spec_.loader.exec_module(mod)
    return mod


def test_lemmas_whose_range_a_slow_host_changes_are_left_free(tmp_path):
    # a (chain, entry, pc) whose q range differs in a run with a slow host waits on the host
    # somewhere in its chain: no lemma there, as the solver's host may wait as long as it likes
    lem = load("place_lemmas")
    sym, _, pl = fsm("spi_controller_mode0", (0, 25, 0))
    top = tmp_path / "top.sv"
    top.write_text(proofgen.place_harness(sym.roles[0], 0, pl, {"LANE_CFG": 0}, [0], []))
    fast, slow = tmp_path / "fast.log", tmp_path / "slow.log"
    fast.write_text("lem 3 5 70 40 70\nlem 3 6 70 50 70\n")
    slow.write_text("lem 3 5 70 40 70\nlem 3 6 70 50 70\nlem 3 6 70 90 70\n")
    lem.main(str(top), str(fast), 0, str(slow))
    text = top.read_text()
    assert f"'d{(3 << 16) | (5 << 8) | 70}: lem_ok = q >= " in text
    assert f"'d{(3 << 16) | (6 << 8) | 70}:" not in text
