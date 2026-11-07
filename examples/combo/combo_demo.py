# SPDX-License-Identifier: Apache-2.0
"""Both lanes at once: a UART transmitter on lane 0 and the Fast-mode I2C
controller on lane 1, built from two specs into one image.

Lane 0 sends the banner on uo_out[0] back to back while lane 1 runs the
I2C write and read of the I2C example against the same simulated target.
The host serves both mailboxes from one polling loop. The UART trace must
be exactly what the UART example measures alone: every edge at the
compiler's cycle, whatever the other lane does.
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(1, os.path.join(os.path.dirname(HERE), "i2c"))
import tse_demo as td  # noqa: E402
import i2c_demo as i2c  # noqa: E402
from tse_host import image  # noqa: E402


class UartTxJob:
    def __init__(self, lane, data):
        self.data = list(data)
        self.tx = td.chip_reg("MBOX_TX", lane)
        self.full = 1 << (2 * lane)

    def done(self):
        return not self.data

    def poll(self, chip, st):
        if self.data and not st & self.full:
            chip.poke(self.tx, self.data.pop(0))
            return True
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--uart", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--i2c", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--image", required=True)
    ap.add_argument("--tx-text", required=True)
    a = ap.parse_args()
    banner = a.tx_text.encode().decode("unicode_escape").encode("latin-1")

    lanes = td.read_tsec(*a.uart)
    lanes.update(td.move_lane(td.read_tsec(*a.i2c), 0, 1))
    pads = dict(i2c.PADS, tx=0)
    inputs = {"scl": (td.UIO, i2c.SCL), "sda": (td.UIO, i2c.SDA)}
    with open(a.image, "wb") as fh:
        fh.write(image.pack(td.build_blocks(lanes, pads, inputs), td.FCLK, "combo"))
    with open(a.image, "rb") as fh:
        blocks = image.unpack(fh.read())[2]

    target = i2c.Target(0x50)
    jobs = [UartTxJob(0, banner), i2c.I2cJob(1, i2c.WRITE + i2c.READ)]
    chip = td.Chip(a.sim)
    chip.pins.hold(td.UIO, i2c.SCL, 1)
    chip.pins.hold(td.UIO, i2c.SDA, 1)
    crc = chip.load(blocks)
    print("loaded: %s, crc 0x%08X, %d blocks" % (a.image, crc, len(blocks)), flush=True)
    st = td.status_fields(chip.start(0b11))
    run_at = chip.pins.cycle
    print("run: lanes 0 and 1 from cycle %d, STATUS %s" % (run_at, st), flush=True)
    chip.pins.attach(i2c.Bus(target, i2c.TR))
    i2c.run_jobs(chip, jobs, run_at + 600000)
    while chip.peek(td.chip_reg("MBOX_STATUS")) & 1:
        chip.wait(i2c.POLL)
    chip.wait(6000)
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    chip.close()

    bad = jobs[1].problems(target)
    print("end: cycle %d, STATUS %s" % (chip.pins.cycle, st))
    print("window: %.9f s" % (run_at / td.FCLK))
    if st["host_err"] or st["halted"] or st["running"] != 0b11:
        bad.append("host error, halted or stopped lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: %d UART bytes handed to lane 0, I2C on lane 1 written and read back" % len(banner))
    return 0


if __name__ == "__main__":
    sys.exit(main())
