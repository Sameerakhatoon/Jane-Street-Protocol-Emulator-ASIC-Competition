"""07: lanes composed into a chip: chip flags, data window, events, config writes."""
from model.chip import Chip
from model.isa import encode as E
from model.lane import LANE_CFG, TIMER_CTL
from model.tests.helpers import mem_of, parked


def test_sig_highest_lane_wins_and_is_seen_next_cycle():
    l0 = parked([E("DLY", d=2), E("SIG", mask=1, val=1)])          # SIG in cycle 4
    l1 = parked([E("DLY", d=1), E("SIG", mask=1, val=0)])          # SIG in cycle 4
    l2 = parked([E("WAIT", pol=1, src=3, idx=0), E("SET", dst=2, imm=9)])
    chip = Chip([mem_of(l0), mem_of(l1), mem_of(l2)]).run(30)
    assert chip.cf & 1 == 0 and chip.lanes[2].seq.x == 0
    l1 = parked([E("DLY", d=1), E("DLY"), E("SIG", mask=1, val=1)])
    chip = Chip([mem_of(parked([E("DLY")])), mem_of(l1), mem_of(l2)])
    seen = []
    for c in range(30):
        chip.step()
        seen.append(chip.lanes[2].seq.x)
    assert chip.cf & 1 and seen.index(9) > 0


def test_data_window_shared_between_lanes():
    writer = parked([E("SET", dst=2, imm=0x5A), E("MOV", dst=4, src=0), E("STW", addr=9)])
    reader = parked([E("DLY", d=60), E("LDW", addr=9), E("MOV", dst=1, src=4)])
    chip = Chip([mem_of(writer), mem_of(reader)]).run(120)
    assert chip.dwin[9] == 0x5A and chip.lanes[1].seq.y == 0x5A


def test_emit_records_timestamp_of_dispatch():
    prog = parked([E("SET", dst=2, imm=0x77), E("EMIT", code=5, delay=2), E("EMIT", code=6)])
    chip = Chip([mem_of(prog)]).run(20)
    assert chip.events == [(2, 0, 5, 0x77), (5, 0, 6, 0x77)]


def test_class_l_write_takes_effect_two_cycles_later():
    # CSRW LANE_CFG in cycle 2 turns side-set on from cycle 4: the DLY side 1 in
    # cycle 5 drives pin 0 (ss_base 0), the parking JMP in cycle 6 drives it back to 0
    prog = parked([E("SET", dst=2, imm=0x40), E("CSRW", addr=LANE_CFG), E("DLY", ss=1)])
    chip = Chip([mem_of(prog)])
    high = []
    for c in range(12):
        chip.step()
        if chip.lanes[0].seq.pins & 1:
            high.append(c + 1)
    assert high == [6]


def test_timer_paces_wait_ev0_and_is_acknowledged():
    cfg = {TIMER_CTL: (1 << 18) | (1 << 17) | 6}
    prog = [E("SET", dst=7, imm=1), E("WAIT", pol=1, src=1, idx=0), E("SET", dst=2, imm=3),
            E("WAIT", pol=1, src=1, idx=0, to=1), E("JMP", cond=6, target=6), E("SET", dst=3, imm=4)]
    chip = Chip([mem_of(parked(prog))], [cfg]).run(60)
    s = chip.lanes[0].seq
    assert s.x == 3 and s.tflag and s.y == 0                      # second wait timed out
