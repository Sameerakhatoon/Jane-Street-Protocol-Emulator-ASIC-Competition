"""The model against the chip: RUN at the end of the 16-cycle frame, the one
configuration bank, the data window at 3 and 4 lanes, the fallback store and the
refusals around the self-test."""
import pytest

from tse_host import frames as F
from tse_host import model, regs_map
from tse_host.link import PinLink
from tse_host.session import Session

FB = regs_map.REGIONS["fallback"]["base"]
DW = regs_map.REGIONS["data"]["base"]
PROG_LO = {k: v[1] for k, v in regs_map.LANE["LANE_PROG"]["fields"].items()}


def lane_prog(lane, window=0, fallback=0):
    return (0x1000 + 0x100 * lane + regs_map.LANE["LANE_PROG"]["addr"],
            [window << PROG_LO["window"] | fallback << PROG_LO["fallback"]])


class Watch(model.Chip):
    """Records the cycle of every change of the stored RUN bits and of the live lanes."""
    changes = []

    def _edge(self):
        run, live = self.run, self.live
        super()._edge()
        if self.run != run:
            self.changes.append(("run", self.cycle))
        if self.live != live:
            self.changes.append(("live", self.cycle))


def loaded(lanes=3, blocks=(), chip_cls=model.Chip, verify=True):
    chip = chip_cls(seed=11, lanes=lanes)
    s = Session(PinLink(chip))
    s.load([(0x0000, [7, 8, 9])] + list(blocks), bist=False, verify=verify)
    return chip, s


# 1: a RUN write is stored at once, but the lanes start at the next frame end,
#    so the start cycle is fixed modulo 16 whatever the write phase
def test_01_run_at_frame_end():
    starts = set()
    for delay in range(16):
        chip, s = loaded(chip_cls=Watch)
        s.leave_load()
        c0 = chip.cycle
        chip.tick(delay)
        chip.changes = []
        s.poke(regs_map.RUN, 1)
        chip.tick(20)
        (_, w), (_, l) = chip.changes
        assert chip.changes[0][0] == "run" and chip.changes[1][0] == "live"
        assert 1 <= l - w <= model.FRAME
        starts.add((l - c0) % model.FRAME)
    assert len(starts) == 1


# 2: STATUS shows live lanes; a lane stops at the frame end after RUN clears
def test_02_stop_at_frame_end():
    chip, s = loaded(chip_cls=Watch)
    s.start(0b101)
    assert chip.running() == 0b101 and s.peek(regs_map.STATUS)[0] >> 8 & 0xF == 0b101
    chip.changes = []
    s.poke(regs_map.RUN, 0)
    chip.tick(20)
    assert [k for k, _ in chip.changes] == ["run", "live"]


# 3: every class-L chip register has its own word of the one bank, {bank, word}
def test_03_config_bank():
    slots = {}
    for name, r in regs_map.CHIP.items():
        if r["class"] == "L":
            for i in range(r.get("count", 1)):
                k = model.bank_slot(name, i)
                assert 0 <= k < 64 and k not in slots
                slots[k] = (name, i)
    assert len(slots) == 43
    writes = [(regs_map.CHIP[n]["addr"] + i, [0xFFFFFFFF]) for n, i in slots.values()]
    for lanes in (3, 4):
        chip, s = loaded(lanes, writes, verify=False)
        for n, i in slots.values():
            r = regs_map.CHIP[n]
            want = 0 if n == "LANE_IN" and i >= 4 * lanes else r["mask"]
            assert chip.chip_config(n, i) == want
            assert s.peek(r["addr"] + i) == [want]


# 4: 3 lanes: the data window is SRAM window 3, writable while lanes run from other
#    windows, refused while a running lane fetches from window 3
def test_04_data_window_3_lanes():
    chip, s = loaded(3, [lane_prog(0, 0), lane_prog(1, 3)])
    s.poke(DW + 2, 0xD00D)
    assert s.peek(0x0182) == [0xD00D]
    s.start(0b001)
    s.poke(DW + 5, 0xBEEF)
    assert s.peek(DW + 5) == [0xBEEF] and s.stat()["host_err"] == 0
    s.poke(regs_map.RUN, 0b011)
    s.poke(DW + 6, 1)
    assert s.peek(DW + 6) == [chip.sram[0x186]] and s.stat()["host_err"] == 1


# 5: 4 lanes: a 16-word store; words above 15 read 0 and ignore writes without an
#    error; the SRAM is closed to the host while lanes run
def test_05_data_window_4_lanes():
    chip, s = loaded(4)
    assert s.peek(regs_map.ID)[0] >> 12 & 7 == 4
    s.start(1)
    s.poke(DW + 15, 0x1515)
    s.poke(DW + 16, 0x1616)
    assert s.peek(DW + 15, 2) == [0x1515, 0] and s.stat()["host_err"] == 0
    assert s.peek(0x0000) == [0] and s.stat()["host_err"] == 1


# 6: the fallback store: writable while running lanes use the SRAM, refused while a
#    running lane fetches from it
def test_06_fallback_store():
    chip, s = loaded(3, [lane_prog(0, 0), lane_prog(1, 0, 1)])
    s.start(0b001)
    s.poke(FB + 3, 0x33)
    assert s.peek(FB + 3) == [0x33] and s.stat()["host_err"] == 0
    s.poke(regs_map.RUN, 0b011)
    s.poke(FB + 3, 0x44)
    assert s.peek(FB + 3) == [0x33] and s.stat()["host_err"] == 1


# 7: the self-test owns the SRAM: host access during it is refused and reads 0;
#    BEGIN clears the result; a dead macro reports its first failing element
def test_07_bist_registers():
    chip = model.Chip(seed=2, sram_dead=True)
    link = PinLink(chip)
    s = Session(link)
    s.enter_load()
    link.exchange(F.begin() + F.bist())
    assert s.peek(0x0010) == [0] and s.stat()["host_err"] == 1
    link.wait(model.BIST_CYCLES)
    st = s.stat()
    assert st["bist_done"] and not st["bist_pass"]
    f = regs_map.CHIP["BIST"]["fields"]
    b = s.peek(regs_map.BIST)[0]
    assert b >> f["fail"][1] & 1 and b >> f["element"][1] & 7 == 2 and b & 0x1FF == 0
    link.exchange(F.begin())
    assert s.peek(regs_map.BIST) == [0] and not s.stat()["bist_done"]


# 8: while a lane runs: its F registers and the chip configuration take writes, its
#    L and P registers are refused
def test_08_running_lane_writes():
    chip, s = loaded()
    s.start(1)
    tx = 0x1000 + regs_map.LANE["TXCTL"]["addr"]
    s.poke(tx, 3)
    s.poke(regs_map.CHIP["EV_CTL"]["addr"], 1)
    assert chip.lane_flop[0]["TXCTL"] == 3 and chip.chip_config("EV_CTL") == 1
    assert s.stat()["host_err"] == 0
    s.poke(0x1000 + regs_map.LANE["CMD"]["addr"], 1)
    assert s.stat()["host_err"] == 1


@pytest.mark.parametrize("lanes", [3, 4])
# 9: the state file round trip keeps the build, the bank and the data store
def test_09_snapshot(lanes):
    chip, s = loaded(lanes, [(regs_map.PIN_OPT, [0xA])])
    chip.dstore[3] = 0x77
    other = model.Chip(seed=99)
    other.restore(chip.snapshot())
    assert other.lanes == lanes and other.chip_config("PIN_OPT") == 0xA
    assert other.dstore[3] == 0x77 and other.loaded == 1
