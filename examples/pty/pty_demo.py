# SPDX-License-Identifier: Apache-2.0
"""The compiled UART on the Verilated chip, reached from a Linux pty.

The simulator runs with its UART pty bridge on the lane pins: the bridge
drives the receive lane's pin (uio_in[2]) with 8N1 frames of every byte a
terminal writes to the pty, and decodes the transmit lane's pad (uo_out[0])
back to the pty. This script loads the image over the byte bus with the
bridge off, starts both lanes, turns the bridge and the VCD on, then plays
the host: every byte the receive lane pushes is read from its mailbox and
written to the transmit lane's mailbox. So whatever picocom (or the test
client) types comes back through two compiled lanes.

    python3 examples/pty/pty_demo.py --sim "sim/build/chip/tse_sim --pin-server --uart \
        --uart-tx uo0 --uart-rx uio2 --vcd t.vcd --vcd-hold" --tsec build/uart uart \
        [--count N] [--seconds S]

It prints 'pty /dev/pts/N' once the lanes run, then serves until N bytes
were echoed or S seconds of wall time passed (0: until Ctrl-C).
"""
import argparse
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tse_demo as td  # noqa: E402

TX_PAD = 0                  # uo_out[0], decoded by the bridge
RX_PIN = (td.UIO, 2)        # uio_in[2], driven by the bridge
POLL = 400


def bitrev8(v):
    return int("{:08b}".format(v & 0xFF)[::-1], 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True)
    ap.add_argument("--tsec", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--count", type=int, default=0, help="stop after this many bytes echoed")
    ap.add_argument("--seconds", type=float, default=0, help="stop after this much wall time")
    a = ap.parse_args()
    stop = []
    signal.signal(signal.SIGINT, lambda *_: stop.append(1))
    signal.signal(signal.SIGTERM, lambda *_: stop.append(1))

    lanes = td.read_tsec(*a.tsec)
    role_lane = {role: lane for lane, (role, _, _, _) in lanes.items()}
    tx_lane, rx_lane = role_lane["tx_side"], role_lane["rx_side"]
    blocks = td.build_blocks(lanes, {"tx": TX_PAD}, {"rx": RX_PIN})
    mbox_tx = td.chip_reg("MBOX_TX", tx_lane)
    mbox_rx = td.chip_reg("MBOX_RX", rx_lane)
    mbox_st = td.chip_reg("MBOX_STATUS")
    tx_full, rx_full = 1 << (2 * tx_lane), 1 << (2 * rx_lane + 1)

    chip = td.Chip(a.sim, log=True)
    pty = chip.pipe.wait_line("pty ").split()[1]
    chip.pipe.adapters(False)
    crc = chip.load(blocks)
    st = td.status_fields(chip.start((1 << tx_lane) | (1 << rx_lane)))
    chip.pipe.vcd_start()
    chip.pipe.adapters(True)
    run_at = chip.pins.cycle
    print("loaded: crc 0x%08X; run: lanes %d and %d from cycle %d, STATUS %s"
          % (crc, tx_lane, rx_lane, run_at, st))
    print("pty %s" % pty, flush=True)

    t0 = time.time()
    got = bytearray()
    echo = []
    while not stop:
        if a.count and len(got) >= a.count and not echo:
            break
        if a.seconds and time.time() - t0 > a.seconds:
            break
        s = chip.peek(mbox_st)
        idle = True
        if s & rx_full:
            b = bitrev8(chip.peek(mbox_rx))
            got.append(b)
            echo.append(b)
            idle = False
        if echo and not s & tx_full:
            chip.poke(mbox_tx, echo.pop(0))
            idle = False
        if idle:
            chip.wait(POLL)
    while chip.peek(mbox_st) & tx_full:
        chip.wait(POLL)
    chip.wait(int(12 * td.FCLK / 115200))
    irq = chip.peek(td.chip_reg("IRQ"))
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    end = chip.pins.cycle
    chip.close()
    stats = [x for x in chip.log() if x.startswith("tse_sim:")]
    print("echoed: %s" % bytes(got))
    print("end: cycle %d (%.3f ms of chip time after RUN), IRQ 0x%04X, STATUS %s"
          % (end, (end - run_at) * 1e3 / td.FCLK, irq, st))
    for x in stats:
        print(x)
    bad = []
    if a.count and len(got) != a.count:
        bad.append("echoed %d of %d bytes" % (len(got), a.count))
    if irq:
        bad.append("interrupt bits 0x%04X" % irq)
    if st["host_err"] or st["halted"] or st["running"] != (1 << tx_lane) | (1 << rx_lane):
        bad.append("host error, halted or stopped lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: %d bytes from the pty echoed through two lanes" % len(got))
    return 0


if __name__ == "__main__":
    sys.exit(main())
