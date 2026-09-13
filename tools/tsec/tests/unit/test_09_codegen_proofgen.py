from fractions import Fraction as Fr

from tools.tsec import proofgen, uppaal
from tools.tsec.codegen import assemble, image_text, listing_lines
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
    assert [w for _, w, _, _, _ in rows] == [0xC300, 0x4207, 0x4010, 0xE040, 0x8000, 0xE03E,
                                            0x0204, 0x4011, 0xE03E, 0x0000]
    lines = listing_lines(rows)
    assert lines[1] == "  0  c300   idle:  PULL  block                    anchor"


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
    top, rows = proofgen.harness(res.role, 1, res.chosen.low)
    assert len(rows) == 1 and rows[0][1:] == (210, 312)
    assert "tse_window_check #(.LO(210), .HI(312)" in top and ".trig_val(1'b0)" in top


def test_uppaal_bounds():
    res = compiled("tx_side")
    xml = uppaal.export(res.role, res.chosen.low)
    assert "x &lt;= 531" in xml and "x &gt;= 511" in xml
