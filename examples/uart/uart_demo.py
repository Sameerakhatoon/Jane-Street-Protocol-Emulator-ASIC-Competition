"""UART both ways on the Verilated chip, driven like a board would drive it.

Turns the tsec build of the UART spec into a TSEI image file, loads that
file over the byte bus, starts both lanes, then over runtime SPI:
  TX: writes each byte of the banner into the transmit lane's mailbox as
      soon as the mailbox is empty, so the lane sends back to back;
  RX: the receive line is driven with back-to-back 8N1 frames on exact
      model cycles, the text three times: at the nominal rate, then
      --rx-skew percent fast, then slow; every byte the receive lane pushes
      is read from its mailbox and written to the transmit lane (an echo).
The harness writes the pins-only VCD that the judges read. This script
checks only what the host sees: the bytes read back, no framing-error
interrupt, no host error, no halted lane. Its "window" line gives the time
the lanes started, from which the judges read the trace.

    python3 examples/uart/uart_demo.py --sim "sim/build/chip/tse_sim --pin-server --vcd t.vcd" \
        --tsec build/uart uart --image build/uart.tsei --tx-text "..." --rx-text "..."
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import tse_demo as td  # noqa: E402
from tse_host import image  # noqa: E402

TX_PAD = 0                  # uo_out[0]
RX_PIN = (td.UIO, 2)        # uio_in[2]: quiet in load mode, so the trace is clean
POLL = 400                  # clocks between mailbox polls when nothing happened


def bitrev8(v):
    return int("{:08b}".format(v & 0xFF)[::-1], 2)


def text(s):
    return s.encode().decode("unicode_escape").encode("latin-1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sim", required=True, help="pin server command line")
    ap.add_argument("--tsec", nargs=2, required=True, metavar=("DIR", "NAME"))
    ap.add_argument("--image", required=True, help="TSEI file to write, then load")
    ap.add_argument("--tx-text", required=True)
    ap.add_argument("--rx-text", required=True)
    ap.add_argument("--baud", type=float, default=115200)
    ap.add_argument("--rx-skew", type=float, default=1.5, help="percent")
    a = ap.parse_args()
    tx_data, rx_data = text(a.tx_text), text(a.rx_text)

    lanes = td.read_tsec(*a.tsec)
    role_lane = {role: lane for lane, (role, _, _, _) in lanes.items()}
    tx_lane, rx_lane = role_lane["tx_side"], role_lane["rx_side"]
    with open(a.image, "wb") as fh:
        fh.write(image.pack(td.build_blocks(lanes, {"tx": TX_PAD}, {"rx": RX_PIN}), td.FCLK, a.tsec[1]))
    with open(a.image, "rb") as fh:
        blocks = image.unpack(fh.read())[2]

    mbox_tx = td.chip_reg("MBOX_TX", tx_lane)
    mbox_rx = td.chip_reg("MBOX_RX", rx_lane)
    mbox_st = td.chip_reg("MBOX_STATUS")
    tx_full, rx_full = 1 << (2 * tx_lane), 1 << (2 * rx_lane + 1)
    bit = td.FCLK / a.baud

    chip = td.Chip(a.sim)
    chip.pins.hold(RX_PIN[0], RX_PIN[1], 1)
    crc = chip.load(blocks)
    print("loaded: %s, crc 0x%08X, %d blocks" % (a.image, crc, len(blocks)))
    st = td.status_fields(chip.start((1 << tx_lane) | (1 << rx_lane)))
    run_at = chip.pins.cycle
    print("run: lanes %d and %d from cycle %d, STATUS %s" % (tx_lane, rx_lane, run_at, st))

    queue = list(tx_data)
    while queue:
        if not chip.peek(mbox_st) & tx_full:
            chip.poke(mbox_tx, queue.pop(0))
        else:
            chip.wait(POLL)
    print("tx: %d bytes handed to lane %d" % (len(tx_data), tx_lane))

    start = chip.pins.cycle + int(20 * bit)
    edges = []
    for skew in (0.0, a.rx_skew, -a.rx_skew):
        edges += td.uart_edges(start, rx_data, a.baud * (1 + skew / 100.0))
        start = edges[-1][0] + int(3 * bit)
    chip.pins.schedule(RX_PIN[0], RX_PIN[1], edges)
    want = rx_data * 3
    got = bytearray()
    echo = []
    limit = start + int(len(want) * 11 * bit)
    while (len(got) < len(want) or echo) and chip.pins.cycle < limit:
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
    chip.wait(int(12 * bit))
    irq = chip.peek(td.chip_reg("IRQ"))
    st = td.status_fields(chip.peek(td.chip_reg("STATUS")))
    end = chip.pins.cycle
    chip.close()

    print("rx: read back %s" % got.hex())
    print("rx: expected  %s (nominal, %+g%%, %+g%%)" % (want.hex(), a.rx_skew, -a.rx_skew))
    print("end: cycle %d, IRQ 0x%04X, STATUS %s" % (end, irq, st))
    print("window: %.9f s" % (run_at / td.FCLK))
    bad = []
    if bytes(got) != want:
        bad.append("rx bytes differ")
    if irq:
        bad.append("interrupt bits 0x%04X (framing error?)" % irq)
    if st["host_err"] or st["halted"] or st["running"] != (1 << tx_lane) | (1 << rx_lane):
        bad.append("host error, halted or stopped lane")
    if bad:
        print("FAIL host: " + "; ".join(bad))
        return 1
    print("PASS host: %d bytes sent, %d received and echoed" % (len(tx_data), len(got)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
