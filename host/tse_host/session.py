"""The load sequence and runtime access, written once for every link.

load(): enter load mode (rst_n high, then low: loaded clears), BEGIN, BIST,
wait, STAT (raise FallbackNeeded if the self-test failed), WRB bursts,
COMMIT, STAT (raise LoadError if refused), RDB every burst and compare.
start(): leave load mode and write RUN over SPI. peek() and poke() use the
byte bus (RD, WR) in load mode and SPI in run mode.

Portable: CPython 3 and MicroPython.
"""
from . import frames, regs_map, spi

BIST_WAIT = 12000       # clocks: the March test runs about 10,300


class LoadError(Exception):
    pass


class FallbackNeeded(LoadError):
    pass


class Session:
    def __init__(self, link):
        self.link = link
        self.load_mode = False

    def enter_load(self):
        self.link.enter_load()
        self.load_mode = True

    def leave_load(self):
        self.link.leave_load()
        self.load_mode = False

    def stat(self):
        if self.load_mode:
            return frames.decode_stat(self.link.exchange(frames.stat())[-1])
        mosi, n = spi.status()
        return frames.decode_stat(self.link.spi(mosi, n)[0])

    def peek(self, addr, count=1):
        if self.load_mode:
            seen = self.link.exchange(frames.rdb(addr, count))
            return frames.words_from(seen, 5, count)
        mosi, n = spi.read_block(addr, count)
        return spi.words(self.link.spi(mosi, n))

    def poke(self, addr, value):
        if self.load_mode:
            self.link.exchange(frames.wr(addr, value))
        else:
            mosi, n = spi.write(addr, value)
            self.link.spi(mosi, n)

    def readback(self, blocks):
        """Compare every word of blocks with the chip; returns [(addr, want, got)]."""
        stream, where = frames.readback(blocks)
        seen = self.link.exchange(stream)
        want = {}
        for addr, words in blocks:
            for i, w in enumerate(words):
                want[addr + i] = w
        bad = []
        for addr, count, first in where:
            for i, got in enumerate(frames.words_from(seen, first, count)):
                if got != want[addr + i]:
                    bad.append((addr + i, want[addr + i], got))
        return bad

    def load(self, blocks, bist=True, verify=True):
        """The whole load. Returns the COMMIT CRC; leaves the chip in load mode."""
        self.enter_load()
        self.link.exchange(frames.prologue(bist))
        if bist:
            self.link.wait(BIST_WAIT)
            st = self.stat()
            if not (st["bist_done"] and st["bist_pass"]):
                raise FallbackNeeded("SRAM self-test failed (BIST 0x%08x)"
                                     % self.peek(regs_map.BIST)[0])
        body, crc = frames.load_body(blocks)
        self.link.exchange(body + frames.commit(crc))
        st = self.stat()
        if not st["loaded"]:
            got = self.peek(regs_map.LOAD_CRC)[0]
            raise LoadError("COMMIT refused: sent 0x%08x, chip has 0x%08x" % (crc, got))
        if verify:
            bad = self.readback(blocks)
            if bad:
                a, w, g = bad[0]
                raise LoadError("readback: %d words differ, first 0x%04x want 0x%08x got 0x%08x"
                                % (len(bad), a, w, g))
        return crc

    def start(self, run_mask):
        """Run mode, then RUN over SPI. Returns the STATUS register after the write."""
        self.leave_load()
        self.poke(regs_map.RUN, run_mask)
        return self.peek(regs_map.STATUS)[0]
