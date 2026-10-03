"""06: SWD judge: line reset, select sequence, reads, writes and faults."""
import os
import tempfile

from hypothesis import given, strategies as st

from verif.judges import swd
from verif.judges.tests.jwave import (Wave, swd_idle, swd_jtag_to_swd, swd_line_reset, swd_op,
                                      swd_request, swd_wave)
from verif.judges.vcd import read_vcd


def judge(cycles):
    w = Wave()
    w.add("swclk", 0)
    w.add("swdio", 1)
    swd_wave(w, cycles)
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=1e-6)
        return swd.decode(read_vcd(path, {"swclk": "swclk", "swdio": "swdio"}, pullup=True))
    finally:
        os.unlink(path)


def test_request_byte_of_a_dpidr_read_is_0xa5():
    bits = swd_request(0, 1, 0x0)
    assert sum(b << k for k, b in enumerate(bits)) == 0xA5


def test_connect_sequence_and_dpidr_read():
    cyc = (swd_line_reset() + swd_jtag_to_swd() + swd_line_reset() + swd_idle()
           + swd_op(0, 1, 0x0, 0x2BA01477) + swd_idle()
           + swd_op(0, 0, 0x4, 0x50000000) + swd_idle()
           + swd_op(1, 1, 0xC, 0x12345678) + swd_idle())
    r = judge(cyc)
    assert r.keys() == ["LINE_RESET", "JTAG_TO_SWD", "LINE_RESET", "DP R 0x0 OK 2BA01477",
                        "DP W 0x4 OK 50000000", "AP R 0xC OK 12345678"]
    assert r.violations == []


def test_wait_and_fault_are_frames_not_violations():
    r = judge(swd_idle() + swd_op(1, 1, 0x0, ack=2) + swd_idle() + swd_op(1, 0, 0x4, ack=4) + swd_idle())
    assert r.keys() == ["AP R 0x0 WAIT", "AP W 0x4 FAULT"] and r.violations == []


def test_no_ack_is_caught():
    r = judge(swd_idle() + swd_op(0, 1, 0x0, ack=None) + swd_idle(4))
    assert r.keys() == ["DP R 0x0 NOACK"] and r.rules() == ["no ACK"]


def test_data_parity_error_is_caught():
    r = judge(swd_idle() + swd_op(0, 1, 0x0, 0x1, bad_data_parity=True) + swd_idle()
              + swd_op(0, 0, 0x8, 0x3, bad_data_parity=True) + swd_idle())
    assert r.rules() == ["data parity", "data parity"]


def test_request_parity_error_is_caught():
    r = judge(swd_idle() + swd_op(0, 1, 0x0, 0x1, bad_req_parity=True) + swd_idle())
    assert r.rules() == ["request parity"]


@given(st.lists(st.tuples(st.integers(0, 1), st.integers(0, 1), st.sampled_from([0, 4, 8, 12]),
                          st.integers(0, 0xFFFFFFFF)), min_size=1, max_size=5))
def test_random_packets(ops):
    cyc = swd_line_reset() + swd_idle()
    for apndp, rnw, addr, data in ops:
        cyc += swd_op(apndp, rnw, addr, data) + swd_idle()
    r = judge(cyc)
    assert r.keys() == ["LINE_RESET"] + ["%s %s 0x%X OK %08X" % ("AP" if a else "DP", "R" if rw else "W", ad, d)
                                         for a, rw, ad, d in ops]
    assert r.violations == []
