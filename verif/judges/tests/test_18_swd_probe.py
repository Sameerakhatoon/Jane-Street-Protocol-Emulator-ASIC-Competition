# SPDX-License-Identifier: Apache-2.0
"""18: SWD as a probe sees it: turnaround count, select sequences, drivers."""
import os
import tempfile

from verif.judges import swd
from verif.judges.tests.jwave import (Wave, swd_idle, swd_jtag_to_swd, swd_line_reset, swd_op,
                                      swd_wave)
from verif.judges.vcd import read_vcd

ALERT = 0x19BC0EA2E3DDAFE986852D956209F392


def judge(cycles, oe=False, **kw):
    w = Wave()
    w.add("swclk", 0)
    w.add("swdio", 1)
    m = {"swclk": "swclk", "swdio": "swdio"}
    if oe:
        w.add("host_oe", 1)
        w.add("target_oe", 0)
        m.update(host_oe="host_oe", target_oe="target_oe")
    swd_wave(w, cycles)
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=1e-6)
        return swd.decode(read_vcd(path, m, pullup=True), **kw)
    finally:
        os.unlink(path)


def host_bits(v, n):
    return [("h", (v >> k) & 1) for k in range(n)]


def test_ack_is_read_one_cycle_after_the_turnaround():
    # the bit read at the rising edge after the park bit is the turnaround;
    # ACK[0] is the next one, as OpenOCD and CMSIS-DAP clock it
    cyc = swd_idle() + swd_op(0, 1, 0x0, 0x0BC11477) + swd_idle()
    assert [c[0] for c in cyc[2:2 + 13]] == ["h"] * 8 + ["z", "t", "t", "t", "t"]
    assert judge(cyc).keys() == ["DP R 0x0 OK 0BC11477"]


def test_a_target_one_cycle_late_is_caught():
    op = swd_op(0, 1, 0x0, 0x0BC11477)
    late = op[:9] + [("z",)] + op[9:]           # a two-cycle turnaround
    r = judge(swd_idle() + late + swd_idle(4))
    assert r.keys() != ["DP R 0x0 OK 0BC11477"] and r.violations


def test_openocd_shutdown_and_dormant_wake_up():
    wake = (host_bits(0xFF, 8) + host_bits(ALERT, 128) + host_bits(0, 4) + host_bits(0x1A, 8))
    cyc = (swd_line_reset() + host_bits(0x33BBBBBA, 31) + wake + swd_line_reset() + swd_idle()
           + swd_op(0, 1, 0x0, 0x0BC11477) + swd_idle()
           + swd_line_reset() + host_bits(0xE3BC, 16) + host_bits(0x5A3C, 16) + swd_idle(8))
    r = judge(cyc)
    assert r.keys() == ["LINE_RESET", "JTAG_TO_DORMANT", "DORMANT_TO_SWD", "LINE_RESET",
                        "DP R 0x0 OK 0BC11477", "LINE_RESET", "SWD_TO_DORMANT"]
    assert r.violations == []


def test_swd_to_jtag_ends_decoding_until_the_next_line_reset():
    cyc = (swd_line_reset() + swd_jtag_to_swd() + swd_line_reset() + swd_idle()
           + swd_op(0, 1, 0x0, 0x0BC11477) + swd_line_reset() + host_bits(0xE73C, 16)
           + host_bits(0x1F, 5) + host_bits(0x0B, 8) + swd_idle(4))
    r = judge(cyc)
    assert r.keys() == ["LINE_RESET", "JTAG_TO_SWD", "LINE_RESET", "DP R 0x0 OK 0BC11477",
                        "LINE_RESET", "SWD_TO_JTAG"]
    assert r.violations == []


def test_selection_alert_with_another_activation_code():
    cyc = host_bits(0xFF, 8) + host_bits(ALERT, 128) + host_bits(0, 4) + host_bits(0x00, 8) + swd_idle(8)
    r = judge(cyc)
    assert r.keys() == ["SELECTION_ALERT"] and r.violations == []


def test_driver_signals_pass_on_a_clean_trace():
    cyc = (swd_line_reset() + swd_idle() + swd_op(0, 1, 0x0, 0x0BC11477) + swd_idle()
           + swd_op(0, 0, 0x4, 0x50000000) + swd_idle() + swd_op(0, 1, 0x4, ack=2) + swd_idle())
    r = judge(cyc, oe=True)
    assert r.keys() == ["LINE_RESET", "DP R 0x0 OK 0BC11477", "DP W 0x4 OK 50000000", "DP R 0x4 WAIT"]
    assert r.violations == []


def test_contention_is_caught():
    op = swd_op(0, 1, 0x0, 0x0BC11477)
    op[8] = ("t", 1)                # the target drives during the turnaround
    r = judge(swd_idle() + op + swd_idle(), oe=True)
    assert r.rules() == ["bus contention"]
    assert judge(swd_idle() + op + swd_idle(), oe=True, overlap=200e-9).violations == []


def test_ack_not_driven_by_the_target_is_caught():
    op = swd_op(0, 1, 0x0, 0x0BC11477)
    op[8:12] = [("h", 0), ("h", 1), ("h", 0), ("h", 0)]     # the host fakes OK
    r = judge(swd_idle() + op + swd_idle(), oe=True)
    assert "driver" in r.rules()
