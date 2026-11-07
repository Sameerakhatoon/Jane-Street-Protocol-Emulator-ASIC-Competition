# SPDX-License-Identifier: Apache-2.0
"""Check mode on the Verilated chip: two monitor lanes watch an I2C bus.

Lane 0 runs i2c_edges.s (SCL edges, SDA changes while SCL is low), lane 1
runs i2c_cond.s (START and STOP); both read SCL and SDA from uio_in[2] and
uio_in[3], which the stand-in controller drives. The host pops every record
over SPI while the bus runs and writes them to records.txt for
host/tse_check; the pins-only VCD goes to the I2C judge.

    check_demo.py --sim "tse_sim --pin-server --vcd OUT/check.vcd" --out OUT CASE

CASE clean: compliant Standard-mode traffic. thd-sta: the same traffic
with a START hold of 188 cycles (3.133 us against 4.0 us), as a PIO I2C
controller that holds START for 3.125 us produces. sweep: one transaction
per step, the START hold shortened by one cycle per step, each step checked
by the margin-sweep driver.
"""
import argparse
import os
import sys

import monitor as m
from standin import Bus
from tse_check import obfile, sweep

TRAFFIC = ["S 50W A 10 A 5A A P",
           "S 50W A 10 A Sr 50R A 7F N P",
           "S 50W A 00 A P"]
SWEEP_HOLD = 245                # START hold of the first sweep step, cycles
SWEEP_STEPS = 10


def load(sim):
    b = m.Bench(sim)
    progs = {}
    for lane, name in ((0, "i2c_edges.s"), (1, "i2c_cond.s")):
        _, progs[lane] = m.assemble(os.path.join(m.HERE, name))
    chip = [m.lane_in(lane, 0, m.SCL) for lane in (0, 1)]
    chip += [m.lane_in(lane, 1, m.SDA) for lane in (0, 1)]
    chip += m.chip_config()
    crc = b.load(progs, chip)
    st = b.start(0b11)
    print("loaded: crc 0x%08X; running %s" % (crc, bin(st["running"])), flush=True)
    return b


def traffic(b, keys, **timing):
    bus = Bus(b.pins.cycle + 2000, **timing)
    for k in keys:
        bus.transaction(k)
    b.drive(bus)
    return bus


def write_words(path, words):
    with open(path, "w") as fh:
        fh.write("".join("%08X\n" % w for w in words))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("case", choices=("clean", "thd-sta", "sweep"))
    a = ap.parse_args()
    clock, obs = obfile.load(open(os.path.join(m.HERE, "i2c_sm.json")).read())

    b = load(a.sim)
    print("window: %.9f s" % (b.run_at / m.td.FCLK))
    buses, keys = [], []
    if a.case == "sweep":
        def capture(off):
            first = len(b.words)
            buses.append(traffic(b, TRAFFIC[:1], t_hd_sta=SWEEP_HOLD - off))
            keys.extend(TRAFFIC[:1])
            return b.chunk(first)
        sw = sweep.run(capture, obs, range(SWEEP_STEPS), stop=False)
        print("sweep: START hold %d cycles minus the offset" % SWEEP_HOLD)
        print("\n".join(sweep.lines(sw, clock)))
    else:
        hold = 188 if a.case == "thd-sta" else None
        timing = {"t_hd_sta": hold} if hold else {}
        buses.append(traffic(b, TRAFFIC, **timing))
        keys = list(TRAFFIC)
    st = b.status()
    b.close()

    write_words(os.path.join(a.out, "records.txt"), b.words)
    with open(os.path.join(a.out, "expect.txt"), "w") as fh:
        fh.write("".join(k + "\n" for k in keys))
    events = m.decode(b.words)
    offs, unpaired = m.latency(events, buses)
    print("records: %d words, %d events, %d dropped" % (len(b.words), len(events), st["dropped"]))
    print("edges: %d reported, %d missing or extra; timestamp minus stand-in cycle: %s"
          % (sum(offs.values()), unpaired, ", ".join("%d (%d events)" % kv for kv in sorted(offs.items()))))
    bad =st["dropped"] or unpaired or len(offs) != 1
    if bad:
        print("FAIL check: dropped, missing or extra records, or uneven latency")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
