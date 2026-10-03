"""11: JTAG judge: TAP state machine, IR and DR scans, IDCODE, broken traces."""
import os
import tempfile

from hypothesis import given, strategies as st

from verif.judges import jtag
from verif.judges.tests.jwave import JtagDev, Wave, jtag_reset, jtag_scan, jtag_wave
from verif.judges.vcd import read_vcd

ROLES = {"tck": "tck", "tms": "tms", "tdi": "tdi", "tdo": "tdo"}


def judge(cycles, devs=None, tdo_force=None, roles=ROLES, extra=None, **kw):
    w = Wave()
    w.add("tck", 0)
    w.add("tms", 1)
    w.add("tdi", 0)
    w.add("tdo", "z")
    jtag_wave(w, cycles, devs if devs is not None else [JtagDev()], tdo_force=tdo_force)
    if extra:
        extra(w)
    fd, path = tempfile.mkstemp(suffix=".vcd")
    os.close(fd)
    try:
        w.write(path, tail=1e-6)
        return jtag.decode(read_vcd(path, roles), **kw)
    finally:
        os.unlink(path)


def test_tap_table_matches_the_standard_diagram():
    # every state reaches Test-Logic-Reset with five TMS ones, and the
    # table has the 16 states with Shift looping on TMS low
    assert len(jtag.NEXT) == 16
    for s in jtag.NEXT:
        t = s
        for _ in range(5):
            t = jtag.NEXT[t][1]
        assert t == jtag.TLR
    for s in jtag.SHIFT + ("Pause-DR", "Pause-IR", jtag.RTI):
        assert jtag.NEXT[s][0] == s


def test_idcode_read_after_reset():
    r = judge(jtag_reset() + jtag_scan(0xFFFFFFFFFFFFFFFF, 64))
    assert r.keys() == ["RESET", "IDCODE 4BA00477"] and r.violations == []


def test_chain_with_bypass_device():
    devs = [JtagDev(0x06431041), JtagDev(has_idcode=False), JtagDev(0x4BA00477)]
    r = judge(jtag_reset() + jtag_scan((1 << 96) - 1, 96), devs)
    # TDO shows the device nearest TDO first
    assert r.keys() == ["RESET", "IDCODE 4BA00477,BYPASS,06431041"] and r.violations == []


def test_ir_then_dr_scan():
    r = judge(jtag_reset() + jtag_scan(0xF, 4, ir=True) + jtag_scan(0x1, 1)
              + jtag_scan(0x1, 4, ir=True) + jtag_scan(0x0, 32))
    assert r.keys() == ["RESET", "IR 4 TDI=F TDO=1", "DR 1 TDI=1 TDO=0",
                        "IR 4 TDI=1 TDO=1", "DR 32 TDI=00000000 TDO=4BA00477"]
    assert r.violations == []


def test_pause_inside_a_scan():
    # Shift 3 bits, Exit1, Pause, Exit2, Shift 5 more bits, Update
    seq = jtag_reset() + [(1, 0), (1, 0), (0, 0), (0, 0)]
    seq += [(0, 1), (0, 1), (1, 1), (0, 0), (0, 0), (1, 0), (0, 0)]
    seq += [(0, 0), (0, 0), (0, 0), (1, 0)]
    seq += [(1, 0), (0, 0)]
    r = judge(seq, devs=[JtagDev()], roles={"tck": "tck", "tms": "tms", "tdi": "tdi"})
    assert r.keys() == ["RESET", "IR 7 TDI=07"] and r.violations == []


def test_trst_resets():
    def extra(w):
        w.add("trst", 1)
        w.set("trst", 0.2e-6, 0)
        w.set("trst", 0.4e-6, 1)
    r = judge([(0, 0)] + jtag_scan(0xF, 4, ir=True), roles=dict(ROLES, trst="trst"), extra=extra)
    assert r.keys()[0] == "RESET" and r.keys()[1].startswith("IR 4")


def test_power_up_reset_option():
    seq = [(0, 0)] + jtag_scan(0, 32)
    assert judge(seq, devs=[JtagDev()]).rules() == ["no TAP reset"]
    r = judge(seq, devs=[JtagDev()], assume_reset=True)
    assert r.keys() == ["IDCODE 4BA00477"] and r.violations == []


def test_tap_misstep_shows_in_the_frames():
    # one TMS low too many in Shift-DR: 33 bits instead of 32
    good = jtag_reset() + jtag_scan(0x1, 4, ir=True) + jtag_scan(0, 32)
    dr_shift = len(jtag_reset()) + 10 + 3
    bad = good[:dr_shift + 5] + [(0, 0)] + good[dr_shift + 5:]
    assert judge(good).keys()[-1].startswith("DR 32 ")
    assert judge(bad).keys()[-1].startswith("DR 33 ")


def test_device_out_of_step_drives_tdo_outside_shift():
    # the device answers one cycle into Exit1-DR, as a misstepped TAP would
    seq = jtag_reset() + jtag_scan(0, 32)
    exit1 = len(jtag_reset()) + 3 + 32
    r = judge(seq, tdo_force={exit1: 1}, tdo_z=True)
    assert "TDO active outside Shift" in r.rules()
    assert judge(seq, tdo_z=True).violations == []


def test_tdo_not_driven_in_shift():
    seq = jtag_reset() + jtag_scan(0, 8)
    shift = len(jtag_reset()) + 3 + 2
    r = judge(seq, tdo_force={shift: None})
    assert r.rules() == ["TDO not driven"]


def test_incomplete_scan():
    r = judge(jtag_reset() + jtag_scan(0, 8)[:6])
    assert r.rules() == ["incomplete scan"]


def test_unknown_tms_loses_the_state():
    def extra(w):
        w.set("tms", 1e-6 + 7 * 100e-9 + 10e-9, "x")
    r = judge(jtag_reset() + [(0, 0)] * 3, extra=extra)
    assert r.rules() == ["unknown level", "no TAP reset"]


def test_reserved_manufacturer_code():
    r = judge(jtag_reset() + jtag_scan((1 << 64) - 1, 64), devs=[JtagDev(0x000000FF)])
    assert r.rules() == ["IDCODE manufacturer"]


@given(st.lists(st.tuples(st.booleans(), st.integers(1, 40), st.integers(0, (1 << 40) - 1)),
                min_size=1, max_size=4))
def test_random_scans(scans):
    seq = jtag_reset() + jtag_scan(0x1, 4, ir=True)
    want = ["RESET", "IR 4 TDI=1 TDO=1"]
    dev = JtagDev()
    for ir, n, v in scans:
        n = 4 if ir else n
        seq += jtag_scan(v & ((1 << n) - 1), n, ir=ir)
    r = judge(seq, devs=[dev])
    keys = r.keys()
    assert keys[:2] == want and len(keys) == 2 + len(scans)
    for (ir, n, v), k in zip(scans, keys[2:]):
        n = 4 if ir else n
        assert k.split()[:2] == ["IR" if ir else "DR", str(n)]
        assert int(k.split("TDI=")[1].split()[0], 16) == v & ((1 << n) - 1)
    assert r.violations == []
