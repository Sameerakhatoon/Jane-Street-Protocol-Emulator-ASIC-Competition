from fractions import Fraction as Fr

from tools.tsec import proofgen, uppaal
from tools.tsec.codegen import assemble, image_text, listing_lines
from tools.tsec.cycles import FetchModel as CyclesFetch
from tools.tsec.fsmsched import Scheduler
from tools.tsec.schedule import compile_role
from tools.tsec.timing import FetchModel

from helpers import FCLK, UART, front, role


def compiled(name, **over):
    sym = front(UART, **over)
    res = compile_role(sym, role(sym, name), 0 if name == "tx_side" else 1, FCLK, FetchModel(), 50)
    return res


def test_assemble_and_listing():
    res = compiled("tx_side", baud=921600)
    rows = assemble(res.chosen)
    assert [w for _, w, _, _, _ in rows] == [0x4011, 0x4111, 0xC300, 0x4207, 0x4010, 0xE040, 0x8000,
                                            0xE03E, 0x0206, 0x4011, 0xE03E, 0x0002]
    lines = listing_lines(rows)
    assert lines[3] == "  2  c300   idle:  PULL  block                    anchor"


def test_image_puts_each_lane_in_its_window():
    res = compiled("tx_side", baud=921600)
    text = image_text("uart", [(0, "tx_side", assemble(res.chosen)), (2, "x", [])])
    assert text.splitlines()[2] == "@000" and "@200" in text


def test_rejection_keeps_every_attempt():
    res = compiled("tx_side", baud=8_000_000)
    assert res.chosen is None and [t.mode for t in res.tried] == ["dly", "timer"]
    assert all(t.report.violations for t in res.tried)


def test_window_bounds_round_inward_and_count_formal_steps():
    assert proofgen.window_bounds(Fr(625, 3), Fr(625, 2)) == (210, 312)
    assert proofgen.window_bounds(Fr(18), Fr(54), d_max=Fr(3, 2)) == (19, 52)


def test_harness_for_the_receiver():
    res = compiled("rx_side")
    sym = front(UART)
    obs = proofgen.frame_obs(sym, res.role, 1, res.chosen.low, res.chosen.labels, FetchModel())
    rows = [w for _, w, _, _, _ in assemble(res.chosen)]
    top, chk = proofgen.harness(res.role, 1, obs, {"LANE_CFG": 0}, rows, [], (0, 0))
    assert len(chk) == 1 and chk[0][1:] == (210, 312)
    assert ".LO(LATE == 0 ? 313 : 210), .HI(312)" in top
    assert ".trig_val(1'b0)" in top and "pc_q == 8'd0" in top
    assert "tse_lane #" in top and "cfg_img[32*tse_regs_pkg::LW_LANE_CFG +: 32]" in top
    # the bank takes the program's writes, starting from the image
    assert "tse_cfg_bank #" in top and ".we(cfg_we)" in top and "assume (cfg == (cfg_img &" in top


def test_a_check_is_abandoned_off_the_path_to_its_act():
    # The I2C target answers ACK or NACK after the same waits: a trigger armed for one answer
    # is abandoned when the address compare takes the other branch.
    with open("specs/i2c_target_fm.tse") as fh:
        sym = front(fh.read())
    att, _ = Scheduler(sym, sym.roles[0], 0, FCLK, CyclesFetch(), (0, 25)).compile()
    obs = proofgen.fsm_obs(sym, sym.roles[0], 0, att, CyclesFetch(), FCLK)
    assert obs and all(o.path and o.act in o.path and set(o.waits) <= o.path for o in obs)
    pairs = [(a, b) for a in obs for b in obs if a is not b and a.waits == b.waits and a.name == b.name]
    assert pairs and all(b.act not in a.path for a, b in pairs)
    top, _ = proofgen.harness(sym.roles[0], 0, obs, {"LANE_CFG": 0}, [0], [], (0, 0))
    assert top.count(".abandon(!(") == len(obs)
    assert f"(ONLY < 0 || ONLY == {len(obs) - 1}) && stay >= 3'd5" in top
    sby = proofgen.sby_text("tgt", 100, n=len(obs))
    assert f"ob{len(obs) - 1} prv" in sby and sby.index("--") < sby.index("ob0 prv")
    assert f"late{len(obs) - 1} prv" in sby and "late0: chparam -set ONLY 0 -set LATE 0 top" in sby


def test_uppaal_bounds():
    res = compiled("tx_side")
    xml = uppaal.export(res.role, res.chosen.low)
    assert "x &lt;= 531" in xml and "x &gt;= 511" in xml
