"""SPI pieces of the SPI demos: stand-ins on the pins, the flash lane's
host, the controller lane's host and a serprog server.

SpiStandIn is a mode 0 controller made of chip input pins (DrivenPins
holds) that reads MISO from a chip output; it is the testbench side of a
target lane. SpiTargetStandIn is a mode 0 target on the uio pads of a
controller lane, attached to DrivenPins as a bus model, so it wakes on
every edge the lane drives. Both stand in for parts on a board.

FlashHost is the host of a spi_flash_target lane. That spec keeps every
piece of state that outlives one chip-select frame in the host: the image,
the write enable latch, the status byte and the JEDEC ID. The lane pushes
the opcode (and address, and program bytes) and pulls the bytes it sends.
The host stages the first answer word before the frame starts, because it
sees the request (from flashrom) before the controller clocks it; that is
the spec's `assume host_ready`. Two rules come from the compiled program:
the mailbox cannot be flushed, so the host hands over exactly the words a
frame needs; and a read or status frame that ends on a word boundary
leaves the lane in a blocking PULL that the CS rise cannot end, so the
host writes one zero word after such a frame to send the lane back to idle.

ControllerHost drives a spi_controller lane one step per mailbox word:
1 select, 2 and a byte, 4 and a word, 3 deselect.

SerprogServer speaks flashrom's serprog protocol on a TCP port and hands
every SPI operation to a function, in lockstep: chip time stands still
while it waits for the client.
"""
import socket
import struct

import tse_demo as td

UO = "uo"


def bitrev(v, n):
    return int("{:0{}b}".format(v & ((1 << n) - 1), n)[::-1], 2)


def read_out(pins, pin):
    """Level of a chip output: (UO, bit) or (UIO, bit)."""
    reg, bit = pin
    if reg == UO:
        return pins.uo_out >> bit & 1
    _, _, out, _ = pins.pins.run_until(0, 0)
    return out >> bit & 1


class SpiStandIn:
    """Mode 0 controller on chip input pins: MOSI changes with the falling
    edge, MISO is read at the end of the low half, just before the rise."""

    def __init__(self, pins, cs, sck, mosi, miso, half, css=None, csd=None):
        self.p = pins
        self.cs, self.sck, self.mosi, self.miso = cs, sck, mosi, miso
        self.half = half
        self.css = css if css is not None else 2 * half
        self.csd = csd if csd is not None else 2 * half

    def idle(self):
        for pin, v in ((self.cs, 1), (self.sck, 0), (self.mosi, 0)):
            self.p.hold(pin[0], pin[1], v)

    def xfer(self, out, nread, between=None):
        """Send out, then nread zero bytes, in one frame; the bytes read
        while the zero bytes went out. between(k) runs after byte k with
        the clock stopped low."""
        p = self.p
        res = []
        p.hold(self.cs[0], self.cs[1], 0)
        lead = self.css
        for k, byte in enumerate(list(out) + [0] * nread):
            acc = 0
            for i in range(7, -1, -1):
                p.hold(self.mosi[0], self.mosi[1], byte >> i & 1)
                p.tick(lead)
                lead = self.half
                acc = acc << 1 | read_out(p, self.miso)
                p.hold(self.sck[0], self.sck[1], 1)
                p.tick(self.half)
                p.hold(self.sck[0], self.sck[1], 0)
            res.append(acc)
            if between:
                between(k)
        p.tick(self.half)
        p.hold(self.cs[0], self.cs[1], 1)
        p.hold(self.mosi[0], self.mosi[1], 0)
        p.tick(self.csd)
        return bytes(res[len(out):]), bytes(res)


class SpiTargetStandIn:
    """Mode 0 target on uio pads driven by a controller lane. answer(rx)
    gives the next byte to send after the bytes rx were received in this
    frame; MISO launches the first bit at the CS fall and each later bit at
    a falling edge. frames collects (mosi bytes, miso bytes) per frame."""

    def __init__(self, cs, sck, mosi, miso, answer):
        self.bits = (cs, sck, mosi)
        self.mask = 1 << cs | 1 << sck | 1 << mosi
        self.miso = miso
        self.answer = answer
        self.prev = (1, 0)
        self.frames = []
        self.on = False

    def next_due(self):
        return None

    def _set(self, pins, v):
        pins.own[self.miso[0]][self.miso[1]] = v
        pins._push()

    def _load(self, pins):
        self.tx = self.answer(self.rx)
        self.sent.append(self.tx)
        self.n = 0
        self._set(pins, self.tx >> 7 & 1)

    def update(self, pins, out, oe):
        cs_b, sck_b, mosi_b = self.bits
        cs = out >> cs_b & 1 if oe >> cs_b & 1 else 1
        sck = out >> sck_b & 1 if oe >> sck_b & 1 else 0
        mosi = out >> mosi_b & 1
        pcs, psck = self.prev
        self.prev = (cs, sck)
        if pcs and not cs:
            self.on = True
            self.rx, self.sent, self.sh = [], [], 0
            self._load(pins)
            return
        if not pcs and cs:
            if self.on:
                self.frames.append((bytes(self.rx), bytes(self.sent[:len(self.rx)])))
            self.on = False
            self._set(pins, 0)
            return
        if not self.on:
            return
        if sck and not psck:
            self.sh = (self.sh << 1 | mosi) & 0xFF
            self.n += 1
            if self.n == 8:
                self.rx.append(self.sh)
        elif psck and not sck:
            if self.n == 8:
                self._load(pins)
            else:
                self._set(pins, self.tx >> (7 - self.n) & 1)


def flash_answer(jedec, image):
    """answer() of a serial NOR flash for SpiTargetStandIn."""
    def answer(rx):
        if not rx:
            return 0
        op, n = rx[0], len(rx)
        if op == 0x9F and n <= 3:
            return jedec >> (8 * (3 - n)) & 0xFF
        if op == 0x05:
            return 0
        if op == 0x03 and n >= 4:
            a = rx[1] << 16 | rx[2] << 8 | rx[3]
            return image[(a + n - 4) % len(image)]
        return 0
    return answer


class Mailbox:
    """One lane's mailboxes as the host sees them over runtime SPI."""

    def __init__(self, chip, lane):
        self.chip = chip
        self.tx = td.chip_reg("MBOX_TX", lane)
        self.rx = td.chip_reg("MBOX_RX", lane)
        self.st = td.chip_reg("MBOX_STATUS")
        self.tx_full, self.rx_full = 1 << (2 * lane), 1 << (2 * lane + 1)

    def status(self):
        return self.chip.peek(self.st)


class FlashHost:
    def __init__(self, chip, lane, jedec, image):
        self.mb = Mailbox(chip, lane)
        self.lane = lane
        self.chip = chip
        self.jedec = jedec
        self.image = image
        self.wel = 0
        self.words = []
        self.want = []
        self.got = []
        self.bad = []
        self.release = False

    def stage(self, out, nread):
        """Before the frame: the words the lane will pull, and the pushes
        it must make."""
        op = out[0] if out else None
        self.op, self.out = op, bytes(out)
        self.words, self.got, self.release = [], [], False
        self.want = [op]
        self.prog = []
        if op == 0x9F:
            self.words = [bitrev(self.jedec, 24)]
        elif op == 0x05:
            self.words = [bitrev(self.wel << 1, 8)] * nread
            self.release = True
        elif op in (0x03, 0x02, 0x20) and len(out) >= 4:
            self.addr = out[1] << 16 | out[2] << 8 | out[3]
            self.want.append(self.addr)
            if op == 0x03:
                data = bytes(self.image[(self.addr + i) % len(self.image)] for i in range(nread))
                data += b"\xff" * (-len(data) % 4)
                self.words = [bitrev(struct.unpack(">I", data[i:i + 4])[0], 32)
                              for i in range(0, len(data), 4)]
                self.release = nread % 4 == 0
            if op == 0x02:
                self.want += list(out[4:])
        if self.words and not self.mb.status() & self.mb.tx_full:
            self.chip.poke(self.mb.tx, self.words.pop(0))

    def service(self):
        """One look at the mailboxes: take a push, hand over a word."""
        s = self.mb.status()
        if s & self.mb.rx_full:
            v = self.chip.peek(self.mb.rx)
            self.got.append(v & (0xFFFFFF if len(self.got) == 1 else 0xFF))
        if self.words and not s & self.mb.tx_full:
            self.chip.poke(self.mb.tx, self.words.pop(0))
            s |= self.mb.tx_full
        return s

    def settle(self, k=None):
        """Until nothing is waiting: no push unread, and the next word (if
        the frame needs one) sits in the mailbox."""
        while True:
            s = self.service()
            if not s & self.mb.rx_full and (not self.words or s & self.mb.tx_full):
                return

    def finish(self):
        """After CS rose: the last pushes, the WEL rules, the release word."""
        self.settle()
        if self.release:
            self.chip.poke(self.mb.tx, 0)
            while self.mb.status() & self.mb.tx_full:
                self.chip.wait(50)
        self.settle()
        if self.got != self.want:
            self.bad.append("op %02X: lane pushed %s, controller sent %s"
                            % (self.op, [hex(x) for x in self.got], [hex(x) for x in self.want]))
        if self.op == 0x06:
            self.wel = 1
        elif self.op == 0x02 and self.wel:
            for i, b in enumerate(self.out[4:]):
                a = (self.addr + i) % len(self.image)
                self.image[a] &= b
            self.wel = 0
        elif self.op == 0x20 and self.wel:
            base = (self.addr % len(self.image)) & ~0xFFF
            for a in range(base, min(base + 4096, len(self.image))):
                self.image[a] = 0xFF
            self.wel = 0


class ControllerHost:
    """Steps of a spi_controller lane; between(k) runs while the clock is
    stopped after each step (the flash lane's host gets its turn there)."""

    def __init__(self, chip, lane):
        self.mb = Mailbox(chip, lane)
        self.chip = chip

    def _put(self, word, between):
        while self.mb.status() & self.mb.tx_full:
            if between:
                between(None)
        self.chip.poke(self.mb.tx, word)

    def _get(self, between):
        while not self.mb.status() & self.mb.rx_full:
            if between:
                between(None)
            else:
                self.chip.wait(50)
        return self.chip.peek(self.mb.rx)

    def xfer(self, out, nread, between=None, wide=False):
        """One frame: select, the bytes of out then nread zero bytes, deselect.
        wide: 32-bit steps for the read bytes (whole words only)."""
        data = list(out) + [0] * nread
        self._put(1, between)
        got = []
        k = 0
        while k < len(data):
            if wide and k >= len(out) and len(data) - k >= 4:
                w = data[k] << 24 | data[k + 1] << 16 | data[k + 2] << 8 | data[k + 3]
                self._put(4, between)
                self._put(bitrev(w, 32), between)
                v = self._get(between)
                got += list(struct.pack(">I", v & 0xFFFFFFFF))
                k += 4
            else:
                self._put(2, between)
                self._put(bitrev(data[k], 8), between)
                got.append(self._get(between) & 0xFF)
                k += 1
            if between:
                between(k)
        self._put(3, between)
        while self.mb.status() & self.mb.tx_full:
            self.chip.wait(50)
        self.chip.wait(200)
        return bytes(got[len(out):]), bytes(got)


ACK, NAK = 0x06, 0x15
CMDS = (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x12, 0x13, 0x14)


class SerprogServer:
    """flashrom's serprog on 127.0.0.1; spi(out, nread) -> bytes read.
    freq(hz) sets the SPI clock and returns the rate really used."""

    def __init__(self, spi, freq, name=b"tse-lanes"):
        self.spi = spi
        self.freq = freq
        self.name = name
        self.ls = socket.socket()
        self.ls.bind(("127.0.0.1", 0))
        self.ls.listen(1)
        self.port = self.ls.getsockname()[1]
        self.ops = 0

    def _rx(self, n):
        b = b""
        while len(b) < n:
            c = self.c.recv(n - len(b))
            if not c:
                raise EOFError
            b += c
        return b

    def serve(self):
        """One client, until it hangs up."""
        self.c, _ = self.ls.accept()
        self.c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        try:
            while True:
                self._one(self._rx(1)[0])
        except EOFError:
            pass
        self.c.close()
        self.ls.close()

    def _one(self, cmd):
        send = self.c.sendall
        if cmd == 0x00:
            send(bytes([ACK]))
        elif cmd == 0x10:
            send(bytes([NAK, ACK]))
        elif cmd == 0x01:
            send(bytes([ACK]) + struct.pack("<H", 1))
        elif cmd == 0x02:
            m = bytearray(32)
            for c in CMDS:
                m[c // 8] |= 1 << (c % 8)
            send(bytes([ACK]) + bytes(m))
        elif cmd == 0x03:
            send(bytes([ACK]) + self.name.ljust(16, b"\0")[:16])
        elif cmd == 0x04:
            send(bytes([ACK]) + struct.pack("<H", 4096))
        elif cmd == 0x05:
            send(bytes([ACK, 0x08]))
        elif cmd == 0x12:
            ok = self._rx(1)[0] & 0x08
            send(bytes([ACK if ok else NAK]))
        elif cmd == 0x14:
            hz = struct.unpack("<I", self._rx(4))[0]
            send(bytes([ACK]) + struct.pack("<I", self.freq(hz)))
        elif cmd == 0x13:
            hdr = self._rx(6)
            slen = hdr[0] | hdr[1] << 8 | hdr[2] << 16
            rlen = hdr[3] | hdr[4] << 8 | hdr[5] << 16
            out = self._rx(slen)
            self.ops += 1
            send(bytes([ACK]) + self.spi(out, rlen))
        else:
            send(bytes([NAK]))
