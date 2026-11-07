# SPDX-License-Identifier: Apache-2.0
"""Monitor lanes on the Verilated chip: shared code of the Check and Trigger demos.

The chip only watches here: SCL and SDA come from the stand-in waveform on
uio_in[2] and uio_in[3], both lanes read them through LANE_IN, their
programs EMIT events, and the host pops the records from EV_POP over the
runtime SPI while the bus runs. Programs are hand-written assembly.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
EXAMPLES = os.path.dirname(HERE)
sys.path.insert(0, EXAMPLES)
sys.path.insert(1, HERE)

import tse_demo as td  # noqa: E402
from tse_check import records  # noqa: E402
from tse_host import image, regs_map  # noqa: E402
from tse_host.link import PinLink  # noqa: E402
from tse_host.session import Session  # noqa: E402
from tools.tseasm import asm  # noqa: E402

SCL, SDA = 2, 3                 # uio_in bits
TRIG_PAD = 7                    # uo_out bit of the trigger-out pad
SPI_HALF = 4                    # SCK half period in clocks, the chip's fastest (f_clk / 8)

# event codes, as the programs define them
CODES = dict(scl_fall=1, scl_rise=2, sda_chg=3, start=4, stop=5, addr=8, data=9)


def assemble(path):
    with open(path) as fh:
        p = asm.assemble(fh.read())
    return p, td.Program(p.window, p.words, p.config, p.start)


def lane_in(lane, vpin, uio_bit):
    return ("LANE_IN", lane * 4 + vpin, td.pin_source(td.UIO, uio_bit))


def field(reg, **v):
    return image.field(regs_map.CHIP[reg], **v)


def chip_config(log_lanes=0b11, trig=None):
    """EV_CTL with logging on, plus the trigger words when trig is given:
    trig = (stages [(lanes, code, code_mask, data, data_mask, window)], TRIG_CTL fields)."""
    cfg = [("EV_CTL", 0, field("EV_CTL", log_en=1, lane_mask=log_lanes))]
    if trig:
        stages, ctl = trig
        for s, (lanes, code, cmask, data, dmask, win) in enumerate(stages):
            cfg.append(("TRIG_MATCH", s, field("TRIG_MATCH", lanes=lanes, code=code, code_mask=cmask,
                                               data=data, data_mask=dmask)))
            cfg.append(("TRIG_WIN", s, field("TRIG_WIN", window=win)))
        cfg.append(("TRIG_CTL", 0, field("TRIG_CTL", last=len(stages) - 1, en=1, **ctl)))
        cfg.append(("PIN_OPT", 0, field("PIN_OPT", trig_pad=1)))
    return cfg


class Bench:
    """The chip behind the pin pipe; SCL and SDA belong to the stand-in."""

    def __init__(self, sim_cmd):
        self.pins = td.DrivenPins(td.BusPipePins(sim_cmd.split()))
        self.session = Session(PinLink(self.pins, spi_half=SPI_HALF))
        self.pins.hold(td.UIO, SCL, 1)
        self.pins.hold(td.UIO, SDA, 1)
        self.words = []
        self.known = 0                  # records known to wait in the FIFO
        self.dropped = 0

    def load(self, progs, chip):
        return self.session.load(image.build(progs, chip))

    def start(self, mask):
        st = td.status_fields(self.session.start(mask))
        self.run_at = self.pins.cycle
        return st

    def peek(self, reg, index=0):
        return self.session.peek(td.chip_reg(reg, index))[0]

    def poke(self, reg, value, index=0):
        self.session.poke(td.chip_reg(reg, index), value)

    def status(self):
        v = self.peek("EV_STATUS")
        f = {k: (v >> lo) & ((1 << (hi - lo + 1)) - 1)
             for k, (hi, lo) in regs_map.CHIP["EV_STATUS"]["fields"].items()}
        self.known, self.dropped = f["count"], f["dropped"]
        return f

    def pop(self):
        """Pop every record now in the FIFO (EV_POP of an empty FIFO repeats a
        stale word, so the count comes first). Returns the number popped."""
        n = 0
        if not self.known:
            self.status()
        while self.known:
            self.words.append(self.peek("EV_POP"))
            self.known -= 1
            n += 1
        return n

    def drive(self, bus, tail=2000):
        """Schedule the stand-in's edges and pop records until the bus is done
        and the FIFO is empty."""
        for line, bit in (("scl", SCL), ("sda", SDA)):
            if bus.edges[line]:
                self.pins.schedule(td.UIO, bit, bus.edges[line])
        end = bus.end() + tail
        while self.pins.cycle < end:
            self.pop()
        while self.pop():
            pass

    def chunk(self, first):
        """The words popped from index first on, led by the last TIME record
        before them, so the chunk decodes to full timestamps by itself."""
        lead = [w for w in self.words[:first] if records.split(w)[1] == records.TIME][-1:]
        return lead + self.words[first:]

    def close(self):
        self.pins.close()


def decode(words):
    return records.decode(words)[0]


def expected(bus):
    """[(cycle, code)] the Check programs should report for the stand-in's
    edges: SCL edges, START (also repeated), STOP, SDA changes while SCL is low."""
    names = {"START": "start", "Sr": "start", "STOP": "stop",
             "SCL_RISE": "scl_rise", "SCL_FALL": "scl_fall"}
    out = [(c, CODES[names[w]]) for c, w in bus.marks]
    scl = dict(bus.edges["scl"])
    level = 1
    for c in sorted(set(scl) | set(dict(bus.edges["sda"]))):
        level = scl.get(c, level)
        if c not in scl and level == 0:
            out.append((c, CODES["sda_chg"]))
    return sorted(out)


def latency(events, buses):
    """{record timestamp minus stand-in edge cycle: count} over every expected
    event, paired in order per code, plus the number of events left unpaired
    on either side. One key and 0 unpaired: every edge was reported, all with
    the same latency."""
    want = {}
    for bus in buses:
        for c, code in expected(bus):
            want.setdefault(code, []).append(c)
    offs, unpaired = {}, 0
    codes = set(want) | set(e.code for e in events if e.code in CODES.values())
    for code in codes:
        got = [e.ts for e in events if e.code == code]
        cyc = want.get(code, [])
        unpaired += abs(len(got) - len(cyc))
        for ts, c in zip(got, cyc):
            offs[ts - c] = offs.get(ts - c, 0) + 1
    return offs, unpaired
