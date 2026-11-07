# SPDX-License-Identifier: Apache-2.0
"""01: the streaming VCD reader: header forms, names, vectors, levels, size."""
import time

import pytest

from verif.judges.tests.jwave import Wave
from verif.judges.vcd import X, Z, VcdError, read_vcd

ICARUS_STYLE = """$date
	Mon Oct 12 10:00:00 2026
$end
$version
	Icarus Verilog
$end
$timescale
	1ps
$end
$scope module tb $end
$var wire 1 ! txd $end
$var reg 8 " bus [7:0] $end
$scope module dut $end
$var wire 1 # txd $end
$var wire 1 $ q [3] $end
$upscope $end
$upscope $end
$enddefinitions $end
$comment some tools put comments here $end
#0
$dumpvars
x!
bx "
z#
0$
$end
#1000
1! b1010 " 1#
#2000
0!
b1x10 "
#2000
1!
#3000 0! 1$
"""


def write(tmp_path, text):
    p = tmp_path / "t.vcd"
    p.write_text(text)
    return str(p)


def test_icarus_style_header_and_body(tmp_path):
    tr = read_vcd(write(tmp_path, ICARUS_STYLE),
                  {"a": "tb.txd", "b": "dut.txd", "c": "bus[1]", "d": "bus[0]", "e": "q[3]", "f": "bus[2]"})
    assert tr.ts == pytest.approx(1e-12)
    assert tr.end == 3000
    assert list(tr["a"].times) == [0, 1000, 3000] and list(tr["a"].levels) == [X, 1, 0]
    assert tr["b"].at(0) == Z and tr["b"].at(1500) == 1
    assert tr["c"].at(1000) == 1 and tr["d"].at(1000) == 0 and tr["f"].at(2000) == X
    assert tr["e"].at(2999) == 0 and tr["e"].at(3000) == 1
    assert tr["a"].before(1000) == X and tr["a"].at(1000) == 1


def test_last_change_at_one_time_wins(tmp_path):
    tr = read_vcd(write(tmp_path, ICARUS_STYLE), {"a": "tb.txd"})
    # the second "#2000" block sets the line back to 1: no change is left at 2000
    assert tr["a"].at(2500) == 1


def test_ambiguous_and_missing_names(tmp_path):
    path = write(tmp_path, ICARUS_STYLE)
    with pytest.raises(VcdError, match="ambiguous"):
        read_vcd(path, {"a": "txd"})
    with pytest.raises(VcdError, match="not found"):
        read_vcd(path, {"a": "rxd"})
    with pytest.raises(VcdError, match="select one bit"):
        read_vcd(path, {"a": "bus"})


def test_pullup_maps_z_to_one(tmp_path):
    tr = read_vcd(write(tmp_path, ICARUS_STYLE), {"b": "dut.txd"}, pullup=True)
    assert tr["b"].at(0) == 1


@pytest.mark.parametrize("unit,mult,spaced,ts", [("ns", 1, False, 1e-9), ("ns", 1, True, 1e-9),
                                                 ("ps", 10, True, 1e-11), ("us", 100, False, 1e-4)])
def test_timescales(tmp_path, unit, mult, spaced, ts):
    w = Wave(unit, mult)
    w.add("p", 0)
    w.set("p", 50 * mult * {"ns": 1e-9, "ps": 1e-12, "us": 1e-6}[unit], 1)
    path = w.write(str(tmp_path / "a.vcd"), spaced=spaced)
    tr = read_vcd(path, {"p": "p"})
    assert tr.ts == pytest.approx(ts)
    assert list(tr["p"].times) == [0, 50]


def test_vector_written_by_the_test_writer(tmp_path):
    w = Wave()
    w.add("uo_out", 0, width=8)
    w.set("uo_out", 10e-9, 0x81)
    w.set("uo_out", 20e-9, 0x01)
    tr = read_vcd(w.write(str(tmp_path / "v.vcd")), {"lo": "uo_out[0]", "hi": "top.uo_out[7]"})
    assert list(tr["lo"].levels) == [0, 1] and list(tr["hi"].times) == [0, 10, 20]


def test_large_file_keeps_only_mapped_signals(tmp_path):
    w = Wave()
    w.add("keep", 1)
    for k in range(8):
        w.add("noise%d" % k, 0)
    for n in range(1, 40001):
        w.set("noise%d" % (n % 8), n * 1e-9, n & 1)
        if n % 1000 == 0:
            w.set("keep", n * 1e-9, (n // 1000) & 1)
    path = w.write(str(tmp_path / "big.vcd"))
    t0 = time.time()
    tr = read_vcd(path, {"k": "keep"})
    assert len(tr["k"]) == 40
    assert list(tr.signals) == ["k"]
    assert time.time() - t0 < 20


def test_no_enddefinitions(tmp_path):
    with pytest.raises(VcdError):
        read_vcd(write(tmp_path, "$timescale 1ns $end\n$scope module a $end\n"), {"a": "x"})
