"""Cycle and pin accurate model of the chip side of the host interface.

What it models, one call of tick() per rising clock edge:
  - rst_n through a 2-flop synchronizer; load mode starts on the synchronized
    falling edge, and that edge clears loaded, the command parser and every
    runtime flop (RUN, mailboxes), before any command arrives.
  - the load-mode byte bus: ui_in data, uio_in[0] toggle strobe (2-flop
    synchronizer plus edge detector, so a pin change acts 2 edges later),
    uio_in[1] frame bit, uo_out readback. Data and frame are sampled at that
    edge, unsynchronized: the host must hold them.
  - the commands WR RD WRB RDB BEGIN BIST COMMIT STAT, a frame byte always
    restarting the parser, writes performed as the data arrives, the running
    CRC-32 over the address and data bytes of WR and WRB, COMMIT setting
    loaded only on a match, reads presenting one byte per strobe MSB first,
    the first byte of each word READ_LAT edges after its strobe is seen.
  - BIST: the March test as a fixed BIST_CYCLES run that leaves the SRAM
    holding the last background.
  - run mode: the SPI target on ui_in[0] CS_N, ui_in[1] SCK, ui_in[2] MOSI,
    uo_out[3] MISO, oversampled through 2-flop synchronizers, with the
    refusal rules (configuration of a running lane, RUN while not loaded,
    BEGIN, BIST and COMMIT while any lane runs) setting the sticky host error.
  - the host-bus register map from regs_map: bits outside fields do not exist.
Lanes themselves are not modelled: a lane "runs" when its RUN bit is set.

Host timing faults (strobe edges closer than MIN_HOLD clocks, data changing
before the loader sampled it) are not hidden: the model does what the RTL
would do, and also lists them in .violations for the tests.
"""
import random

from . import regs_map
from .frames import WR, RD, WRB, RDB, BEGIN, BIST, COMMIT, STAT, crc32_update

LANES = 3
SRAM_WORDS = 512
WINDOW_WORDS = 0x80
DATA_WINDOW = 3            # 6x4: window 3 is the data window
READ_LAT = 3               # edges from a read strobe event to the byte on uo_out
BIST_CYCLES = 10300
BIST_LAST_BACKGROUND = 0x55555555
MIN_HOLD = 4
ID_VALUE = 0x54 | (1 << 8) | (LANES << 12) | (1 << 15) | (4 << 16)

P_IDLE, P_ADDR, P_COUNT, P_WDATA, P_READ, P_CRC, P_STAT = range(7)


def _rand_regs(rng, table, cls):
    out = {}
    for name, r in table.items():
        if r["class"] == cls:
            out[name] = rng.getrandbits(32) & r["mask"]
    return out


class Chip:
    def __init__(self, seed=None, lanes=LANES, bist_fail_word=None):
        self.lanes = lanes
        self.bist_fail_word = bist_fail_word
        self.power_up(seed)

    # ---------------------------------------------------------------- power
    def power_up(self, seed=None):
        """Every storage element random, as on a freshly powered tile."""
        rng = random.Random(seed)
        bit = lambda: rng.getrandbits(1)
        self.cycle = 0
        self.violations = []
        self.sram = [rng.getrandbits(32) for _ in range(SRAM_WORDS)]
        self.fallback = [rng.getrandbits(32) for _ in range(regs_map.REGIONS["fallback"]["size"])]
        self.lane_cfg = [_rand_regs(rng, regs_map.LANE, "L") for _ in range(self.lanes)]
        self.lane_flop = [_rand_regs(rng, regs_map.LANE, "F") for _ in range(self.lanes)]
        self.chip_cfg = {}
        for name, r in regs_map.CHIP.items():
            if r["class"] == "L":
                for i in range(r.get("count", 1)):
                    self.chip_cfg[(name, i)] = rng.getrandbits(32) & r["mask"]
        self.run = rng.getrandbits(4)
        self.mbox_tx = [rng.getrandbits(32) for _ in range(4)]
        self.events = []
        self.ts = rng.getrandbits(32)
        # loader flops
        self.lm = [bit(), bit(), bit()]
        self.st = [bit(), bit(), bit()]
        self.loaded = bit()
        self.crc = rng.getrandbits(32)
        self.phase = rng.randrange(7)
        self.code = rng.getrandbits(8)
        self.addr = rng.getrandbits(16)
        self.count = rng.getrandbits(16)
        self.nbyte = rng.getrandbits(2)
        self.acc = rng.getrandbits(32)
        self.rbuf = rng.getrandbits(32)
        self.dout = rng.getrandbits(8)
        self.re_cnt = 0
        self.re_addr = 0
        self.bist_left = 0
        self.bist_done, self.bist_pass, self.bist_info = bit(), bit(), 0
        self.host_err = bit()
        # SPI target flops
        self.sp_sync = [[bit(), bit(), bit()] for _ in range(3)]
        self.miso = bit()
        self._spi_reset()
        # pins, as the board drives them
        self.ui_in, self.uio_in, self.rst_n = 0, 0, 1
        self._strobe_cycle = -MIN_HOLD
        self._strobe_data = (0, 0)

    # ----------------------------------------------------------------- pins
    def set_inputs(self, ui_in=None, uio_in=None, rst_n=None):
        if ui_in is not None:
            self.ui_in = ui_in & 0xFF
        if rst_n is not None:
            self.rst_n = 1 if rst_n else 0
        if uio_in is not None:
            uio_in &= 0xFF
            if (uio_in ^ self.uio_in) & 1:
                gap = self.cycle - self._strobe_cycle
                if gap < MIN_HOLD:
                    self.violations.append((self.cycle, "strobe edges %d clocks apart" % gap))
                self._strobe_cycle = self.cycle
                self._strobe_data = (self.ui_in, (uio_in >> 1) & 1)
            self.uio_in = uio_in

    @property
    def load_mode(self):
        return self.lm[1]

    @property
    def uo_out(self):
        if self.lm[1]:
            return self.dout
        return self.miso << 3

    def tick(self, n=1):
        for _ in range(n):
            self._edge()

    # ------------------------------------------------------------ the clock
    def _edge(self):
        self.cycle += 1
        load_mode = self.lm[1]
        entering = load_mode and not self.lm[2]
        strobe = load_mode and (self.st[2] ^ self.st[1])
        self.lm = [0 if self.rst_n else 1, self.lm[0], self.lm[1]]
        self.st = [self.uio_in & 1, self.st[0], self.st[1]]
        if self.bist_left:
            self.bist_left -= 1
            if not self.bist_left:
                self._bist_finish()
        if not load_mode:
            self.ts = (self.ts + 1) & 0xFFFFFFFF
            self.phase = P_IDLE
            self._spi_edge()
            return
        if entering:
            self.loaded = 0
            self.phase = P_IDLE
            self.re_cnt = 0
            self._engine_reset()
            return
        if self.re_cnt:
            self.re_cnt -= 1
            if not self.re_cnt:
                w = self.bus_read(self.re_addr)
                self.dout = w >> 24
                self.rbuf = (w << 8) & 0xFFFFFFFF
        if strobe:
            if self._strobe_data != (self.ui_in, (self.uio_in >> 1) & 1):
                self.violations.append((self.cycle, "data changed before it was sampled"))
            self._byte(self.ui_in, (self.uio_in >> 1) & 1)

    def _engine_reset(self):
        self.run = 0
        for f in self.lane_flop:
            for k in f:
                f[k] = 0
        self.mbox_tx = [0, 0, 0, 0]
        self.events = []
        self.ts = 0
        self._spi_reset()
        self.miso = 0

    def _byte(self, b, frame):
        if frame:
            self.code, self.nbyte, self.acc = b, 0, 0
            if b in (WR, RD, WRB, RDB):
                self.phase = P_ADDR
            elif b == BEGIN:
                self._begin()
                self.phase = P_IDLE
            elif b == BIST:
                self._bist_start()
                self.phase = P_IDLE
            elif b == COMMIT:
                self.phase = P_CRC
            elif b == STAT:
                self.phase = P_STAT
            else:
                self.phase = P_IDLE
            return
        ph = self.phase
        write = self.code in (WR, WRB)
        if ph == P_ADDR:
            if write:
                self.crc = crc32_update(self.crc, (b,))
            self.addr = ((self.addr << 8) | b) & 0xFFFF
            self.nbyte += 1
            if self.nbyte == 2:
                self.nbyte, self.count = 0, 1
                if self.code in (WRB, RDB):
                    self.phase = P_COUNT
                else:
                    self.phase = P_WDATA if write else P_READ
        elif ph == P_COUNT:
            self.count = ((self.count << 8) | b) & 0xFFFF
            self.nbyte += 1
            if self.nbyte == 2:
                self.nbyte = 0
                if self.count == 0:
                    self.phase = P_IDLE
                else:
                    self.phase = P_WDATA if write else P_READ
        elif ph == P_WDATA:
            self.crc = crc32_update(self.crc, (b,))
            self.acc = ((self.acc << 8) | b) & 0xFFFFFFFF
            self.nbyte += 1
            if self.nbyte == 4:
                self.nbyte = 0
                self.bus_write(self.addr, self.acc)
                self.addr = (self.addr + 1) & 0xFFFF
                self.count -= 1
                if not self.count:
                    self.phase = P_IDLE
        elif ph == P_READ:
            if self.nbyte == 0:
                self.re_cnt, self.re_addr = READ_LAT, self.addr
                self.addr = (self.addr + 1) & 0xFFFF
            else:
                self.dout = self.rbuf >> 24
                self.rbuf = (self.rbuf << 8) & 0xFFFFFFFF
            self.nbyte += 1
            if self.nbyte == 4:
                self.nbyte = 0
                self.count -= 1
                if not self.count:
                    self.phase = P_IDLE
        elif ph == P_CRC:
            self.acc = ((self.acc << 8) | b) & 0xFFFFFFFF
            self.nbyte += 1
            if self.nbyte == 4:
                self.loaded = 1 if self.acc == self.crc ^ 0xFFFFFFFF else 0
                self.phase = P_IDLE
        elif ph == P_STAT:
            self.dout = self.status() & 0xFF
            self.phase = P_IDLE

    def _begin(self):
        self.loaded = 0
        self.run = 0
        self.crc = 0xFFFFFFFF
        self.host_err = 0
        self.fallback = [0] * len(self.fallback)
        for c in self.lane_cfg:
            for k in c:
                c[k] = 0
        for k in self.chip_cfg:
            self.chip_cfg[k] = 0

    def _bist_start(self):
        self.bist_left = BIST_CYCLES
        self.bist_done = self.bist_pass = 0
        self.bist_info = 0

    def _bist_finish(self):
        self.sram = [BIST_LAST_BACKGROUND] * SRAM_WORDS
        self.bist_done = 1
        w = self.bist_fail_word
        self.bist_pass = 1 if w is None else 0
        self.bist_info = 0 if w is None else (1 << 13) | (w & 0x1FF)

    # ------------------------------------------------------------- host bus
    def status(self):
        running = self.run & ((1 << self.lanes) - 1) if self.loaded else 0
        return (self.loaded | self.bist_done << 1 | self.bist_pass << 2 | self.host_err << 3
                | running << 8)

    def running(self, lane=None):
        mask = self.status() >> 8
        return mask if lane is None else (mask >> lane) & 1

    def _decode(self, addr):
        """('sram', i) | ('fallback', i) | ('lane', lane, name) | ('chip', name, index) | None"""
        R = regs_map.REGIONS
        if addr < R["sram"]["base"] + R["sram"]["size"]:
            return ("sram", addr)
        d = R["data"]
        if d["base"] <= addr < d["base"] + d["size"]:
            return ("sram", WINDOW_WORDS * DATA_WINDOW + addr - d["base"])
        f = R["fallback"]
        if f["base"] <= addr < f["base"] + f["size"]:
            return ("fallback", addr - f["base"])
        w = regs_map.LANE_WINDOW
        if w["host_base"] <= addr < w["host_base"] + w["stride"] * self.lanes:
            lane, off = divmod(addr - w["host_base"], w["stride"])
            for name, r in regs_map.LANE.items():
                if r["addr"] == off:
                    return ("lane", lane, name)
            return None
        for name, r in regs_map.CHIP.items():
            n, s = r.get("count", 1), r.get("stride", 1)
            if r["addr"] <= addr < r["addr"] + n * s and (addr - r["addr"]) % s == 0:
                return ("chip", name, (addr - r["addr"]) // s)
        return None

    def _window_lane(self, word):
        """Lane whose program window holds SRAM word, or None."""
        win = word // WINDOW_WORDS
        for lane in range(self.lanes):
            prog = self.lane_cfg[lane].get("LANE_PROG", 0)
            if (prog >> 8) & 3 == win:
                return lane
        return None

    def _refuse(self):
        self.host_err = 1

    def bus_write(self, addr, value):
        where = self._decode(addr)
        if where is None:
            return
        kind = where[0]
        if kind in ("sram", "fallback") and self.bist_left:
            return self._refuse()
        if kind == "sram":
            word = where[1]
            lane = self._window_lane(word)
            if word // WINDOW_WORDS != DATA_WINDOW and lane is not None and self.running(lane):
                return self._refuse()
            self.sram[word] = value
        elif kind == "fallback":
            if self.running():
                return self._refuse()
            self.fallback[where[1]] = value
        elif kind == "lane":
            lane, name = where[1], where[2]
            r = regs_map.LANE[name]
            if r["class"] == "R":
                return
            if self.running(lane):
                return self._refuse()
            if r["class"] == "L":
                self.lane_cfg[lane][name] = value & r["mask"]
            elif r["class"] == "F":
                self.lane_flop[lane][name] = value & r["mask"]
        else:
            name, idx = where[1], where[2]
            r = regs_map.CHIP[name]
            if r["class"] == "R":
                return
            if name == "RUN":
                if self.load_mode:
                    return              # runtime flops are held in reset in load mode
                if not self.loaded:
                    return self._refuse()
                self.run = value & r["mask"]
            elif name == "MBOX_TX":
                self.mbox_tx[idx] = value & r["mask"]
            elif self.running():
                self._refuse()
            elif r["class"] == "L":
                self.chip_cfg[(name, idx)] = value & r["mask"]
            elif name == "EV_CLEAR" and value & 1:
                self.events = []

    def bus_read(self, addr):
        where = self._decode(addr)
        if where is None:
            return 0
        kind = where[0]
        if kind == "sram":
            return self.sram[where[1]]
        if kind == "fallback":
            return self.fallback[where[1]]
        if kind == "lane":
            lane, name = where[1], where[2]
            cls = regs_map.LANE[name]["class"]
            if cls == "L":
                return self.lane_cfg[lane][name]
            if cls == "F":
                return self.lane_flop[lane][name]
            if name == "STATUS":
                return self.running(lane) << 10
            if name == "TS":
                return self.ts
            return 0                    # P reads 0; the other R registers are not modelled
        name, idx = where[1], where[2]
        r = regs_map.CHIP[name]
        if r["class"] == "L":
            return self.chip_cfg[(name, idx)]
        if name == "ID":
            return ID_VALUE
        if name == "RUN":
            return self.run
        if name == "STATUS":
            return self.status()
        if name == "BIST":
            return self.bist_info
        if name == "LOAD_CRC":
            return self.crc ^ 0xFFFFFFFF
        if name == "TS":
            return self.ts
        if name == "EV_STATUS":
            return min(len(self.events), 15)
        if name == "EV_POP":
            return self.events.pop(0) if self.events else 0
        if name == "MBOX_TX":
            return 0
        return 0

    def lane_config(self, lane, name):
        """Backdoor read of a configuration word (programs and the host cannot read them)."""
        return self.lane_cfg[lane][name]

    # ----------------------------------------------------------- SPI target
    def _spi_reset(self):
        self.sp_bits = 0
        self.sp_acc = 0
        self.sp_bytes = []
        self.sp_reply = None        # function: reply bit index -> bit
        self.sp_reply_start = 0
        self.sp_words = []

    def _spi_edge(self):
        cs = (self.ui_in >> 0) & 1
        sck = (self.ui_in >> 1) & 1
        mosi = (self.ui_in >> 2) & 1
        s_cs, s_sck, s_mosi = self.sp_sync
        cs_now, sck_now, sck_old, mosi_now = s_cs[1], s_sck[1], s_sck[2], s_mosi[1]
        self.sp_sync = [[cs, s_cs[0], s_cs[1]], [sck, s_sck[0], s_sck[1]],
                        [mosi, s_mosi[0], s_mosi[1]]]
        if self.chip_cfg.get(("PIN_OPT", 0), 0) & 1:
            return                      # host_spi_off: the pins belong to the crossbar
        if cs_now:
            if self.sp_bits:
                self._spi_reset()
            return
        if sck_now and not sck_old:
            self.sp_acc = ((self.sp_acc << 1) | mosi_now) & 0xFF
            self.sp_bits += 1
            if self.sp_bits % 8 == 0:
                self._spi_byte(self.sp_acc)
        elif sck_old and not sck_now and self.sp_reply is not None:
            i = self.sp_bits - self.sp_reply_start
            if i >= 0:
                self.miso = self.sp_reply(i)

    def _spi_byte(self, b):
        bs = self.sp_bytes
        bs.append(b)
        n, code = len(bs), bs[0]
        stopped = not self.running()
        if code == STAT and n == 1:
            st = self.status() & 0xFF
            self._spi_reply(8, lambda i: (st >> (7 - i)) & 1 if i < 8 else 0)
        elif code == RD and n == 3:
            w = self.bus_read((bs[1] << 8) | bs[2])
            self._spi_reply(32, lambda i: (w >> (31 - i)) & 1 if i < 32 else 0)
        elif code == RDB and n == 5:
            base, count = (bs[1] << 8) | bs[2], (bs[3] << 8) | bs[4]
            words = self.sp_words

            def bit(i):
                k = i // 32
                if k >= count:
                    return 0
                while len(words) <= k:
                    words.append(self.bus_read((base + len(words)) & 0xFFFF))
                return (words[k] >> (31 - i % 32)) & 1
            self._spi_reply(48, bit)
        elif code == WR and 1 < n <= 7:
            self.crc = crc32_update(self.crc, (b,))
            if n == 7:
                self.bus_write((bs[1] << 8) | bs[2],
                               (bs[3] << 24) | (bs[4] << 16) | (bs[5] << 8) | bs[6])
        elif code == WRB:
            if n in (2, 3) or n > 5:
                self.crc = crc32_update(self.crc, (b,))
            if n > 5 and (n - 5) % 4 == 0:
                k = (n - 5) // 4 - 1
                if k < ((bs[3] << 8) | bs[4]):
                    w = (bs[n - 4] << 24) | (bs[n - 3] << 16) | (bs[n - 2] << 8) | bs[n - 1]
                    self.bus_write(((bs[1] << 8) | bs[2]) + k & 0xFFFF, w)
        elif code in (BEGIN, BIST) and n == 1:
            if not stopped:
                self._refuse()
            elif code == BEGIN:
                self._begin()
            else:
                self._bist_start()
        elif code == COMMIT and n == 5:
            if not stopped:
                self._refuse()
            else:
                got = (bs[1] << 24) | (bs[2] << 16) | (bs[3] << 8) | bs[4]
                self.loaded = 1 if got == self.crc ^ 0xFFFFFFFF else 0

    def _spi_reply(self, start_bit, fn):
        self.sp_reply_start = start_bit
        self.sp_reply = fn

    # ------------------------------------------------------------- snapshot
    def snapshot(self):
        """Architectural state as plain data (for the command line's state file)."""
        return {
            "sram": list(self.sram), "fallback": list(self.fallback),
            "lane_cfg": [dict(c) for c in self.lane_cfg],
            "lane_flop": [dict(f) for f in self.lane_flop],
            "chip_cfg": [[k[0], k[1], v] for k, v in sorted(self.chip_cfg.items())],
            "run": self.run, "loaded": self.loaded, "crc": self.crc, "host_err": self.host_err,
            "bist": [self.bist_done, self.bist_pass, self.bist_info],
            "mbox_tx": list(self.mbox_tx), "events": list(self.events),
            "rst_n": self.rst_n, "cycle": self.cycle, "ts": self.ts,
        }

    def restore(self, s):
        self.sram, self.fallback = list(s["sram"]), list(s["fallback"])
        self.lane_cfg = [dict(c) for c in s["lane_cfg"]]
        self.lane_flop = [dict(f) for f in s["lane_flop"]]
        self.chip_cfg = {(n, i): v for n, i, v in s["chip_cfg"]}
        self.run, self.loaded, self.crc, self.host_err = s["run"], s["loaded"], s["crc"], s["host_err"]
        self.bist_done, self.bist_pass, self.bist_info = s["bist"]
        self.mbox_tx, self.events = list(s["mbox_tx"]), list(s["events"])
        self.cycle, self.ts = s["cycle"], s["ts"]
        self.rst_n = s["rst_n"]
        lm = 0 if self.rst_n else 1
        self.lm = [lm, lm, lm]          # settled: no load-mode entry edge on restore
        self.st = [self.uio_in & 1] * 3
        self.phase, self.re_cnt, self.bist_left = P_IDLE, 0, 0
        self._spi_reset()
        self.sp_sync = [[1, 1, 1], [0, 0, 0], [0, 0, 0]]
