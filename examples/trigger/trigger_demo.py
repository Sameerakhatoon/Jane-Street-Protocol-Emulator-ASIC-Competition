# SPDX-License-Identifier: Apache-2.0
"""Trigger mode on the Verilated chip: a 3-stage trigger on decoded I2C events.

Lane 0 decodes bytes (i2c_bytes.s: ADDR, DATA with the byte as data), lane 1
reports START and STOP (the Check program i2c_cond.s). The trigger fires on

    stage 0  ADDR 0xA0 on lane 0          (write to 0x50)
    stage 1  DATA 0x10 on lane 0          within WINDOW cycles (the next byte)
    stage 2  STOP on lane 1               within WINDOW cycles (at most one more byte)

and then pulses the trigger-out pad uo_out[7] for WIDTH cycles, logs a TRIG
record and re-arms. The stand-in sends near misses and two matching writes
at Standard-mode speed (100 kHz).

    trigger_demo.py --sim "tse_sim --pin-server --vcd OUT/trigger.vcd" --out OUT
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "check"))

import monitor as m  # noqa: E402
from standin import Bus  # noqa: E402
from tse_check import records  # noqa: E402

TIMING = dict(t_low=300, t_high=300)    # 100 kHz
WINDOW = 8000                           # cycles: one byte is 5,400
WIDTH = 4
TRAFFIC = [
    ("S 52W A 10 A 5A A P", "other address"),
    ("S 50W A 11 A 5A A P", "other register"),
    ("S 50W A 10 A 5A A P", "match"),
    ("S 50W A 33 A 10 A 5A A P", "0x10 one byte late"),
    ("S 50W A 10 A 5A A 6B A P", "STOP one byte late"),
    ("S 50W A 10 A Sr 50R A 7F N P", "repeated START, no STOP in time"),
    ("S 50W A 10 A 00 A P", "match"),
]


def trigger(window):
    code = 0x3F
    stages = [(0b01, m.CODES["addr"], code, 0xA0, 0xFF, 0),
              (0b01, m.CODES["data"], code, 0x10, 0xFF, window),
              (0b10, m.CODES["stop"], code, 0x00, 0x00, window)]
    return stages, dict(rearm=1, width_m1=WIDTH - 1, pol=1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", type=int, default=WINDOW, help="stage 1 and 2 windows, 0 for none")
    a = ap.parse_args()

    b = m.Bench(a.sim)
    _, p0 = m.assemble(os.path.join(HERE, "i2c_bytes.s"))
    _, p1 = m.assemble(os.path.join(m.HERE, "i2c_cond.s"))
    chip = [m.lane_in(0, 0, m.SDA), m.lane_in(0, 1, m.SCL),
            m.lane_in(1, 0, m.SCL), m.lane_in(1, 1, m.SDA)]
    chip += m.chip_config(trig=trigger(a.window))
    crc = b.load({0: p0, 1: p1}, chip)
    st = b.start(0b11)
    print("loaded: crc 0x%08X; running %s" % (crc, bin(st["running"])))
    print("window: %.9f s" % (b.run_at / m.td.FCLK))

    bus = Bus(b.pins.cycle + 2000, **TIMING)
    for key, _ in TRAFFIC:
        bus.transaction(key)
    b.drive(bus)
    ev_st = b.status()
    b.close()

    with open(os.path.join(a.out, "expect.txt"), "w") as fh:
        fh.write("".join(k + "\n" for k, _ in TRAFFIC))
    with open(os.path.join(a.out, "records.txt"), "w") as fh:
        fh.write("".join("%08X\n" % w for w in b.words))

    events = m.decode(b.words)
    names = {v: k.upper() for k, v in m.CODES.items()}
    stops = {}
    with open(os.path.join(a.out, "events.txt"), "w") as fh:
        for e in events:
            if e.code == records.TRIG:
                what = "TRIG lanes 0x%X stage %d" % records.trig(e.data)
            else:
                what = "%s 0x%02X" % (names.get(e.code, "code %d" % e.code), e.data)
            fh.write("%10d  lane %d  %s\n" % (e.ts, e.lane, what))
            if e.code == m.CODES["stop"]:
                stops[e.ts] = e
    trigs = [e for e in events if e.code == records.TRIG]
    bytes_seen = [e.data for e in events if e.code in (m.CODES["addr"], m.CODES["data"])]
    want_bytes = []
    for key, _ in TRAFFIC:
        for tok in key.split():
            if len(tok) == 3 and tok[2] in "WR":
                want_bytes.append(int(tok[:2], 16) << 1 | (tok[2] == "R"))
            elif len(tok) == 2 and tok != "Sr":
                want_bytes.append(int(tok, 16))
    print("records: %d words, %d events, %d dropped" % (len(b.words), len(events), ev_st["dropped"]))
    print("decoded bytes: %s" % " ".join("%02X" % x for x in bytes_seen))
    print("EV_STATUS: armed %d, fired %d, stage %d" % (ev_st["armed"], ev_st["fired"], ev_st["stage"]))
    bad = []
    if bytes_seen != want_bytes:
        bad.append("decoded bytes differ")
    for e in trigs:
        lanes, stage = records.trig(e.data)
        same = e.ts in stops
        print("TRIG t=%d  lanes 0x%X  stage %d  %s" % (e.ts, lanes, stage,
              "same cycle as the STOP record" if same else "NO STOP record at this cycle"))
        if not same or (lanes, stage) != (0b10, 2):
            bad.append("TRIG record at %d" % e.ts)
    with open(os.path.join(a.out, "trig.txt"), "w") as fh:
        fh.write("".join("%d\n" % e.ts for e in trigs))
    want = sum(1 for _, why in TRAFFIC if why == "match")
    if len(trigs) != want:
        bad.append("%d TRIG records, %d matching writes" % (len(trigs), want))
    if ev_st["dropped"]:
        bad.append("dropped records")
    if bad:
        print("FAIL trigger: " + "; ".join(bad))
        return 1
    print("PASS trigger records: %d TRIG records for %d matching writes among %d transactions"
          % (len(trigs), want, len(TRAFFIC)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
