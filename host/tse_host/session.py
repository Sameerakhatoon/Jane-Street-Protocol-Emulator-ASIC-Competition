"""The load sequence and runtime access, written once for every link.

load(): enter load mode (rst_n high, then low: loaded clears), BEGIN, BIST,
wait, STAT (raise FallbackNeeded if the self-test failed), WRB bursts,
COMMIT, STAT (raise LoadError if refused), RDB every burst and compare.
boot(): the power-up flow with a fallback image: the same steps, but when
the self-test fails it loads the fallback image (into the fallback store,
which needs no SRAM) instead of the SRAM image, and reports which ran.
start(): leave load mode and write RUN over SPI. peek() and poke() use the
byte bus (RD, WR) in load mode and SPI in run mode.

Portable: CPython 3 and MicroPython.
"""
from . import frames, image, regs_map, spi

BIST_WAIT = 13000       # clocks: the self-test runs about 11,500


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

    def selftest(self):
        """Enter load mode, BEGIN, BIST, wait. Returns 0 on a pass, else the BIST register."""
        self.enter_load()
        self.link.exchange(frames.prologue(True))
        self.link.wait(BIST_WAIT)
        st = self.stat()
        if st["bist_done"] and st["bist_pass"]:
            return 0
        return self.peek(regs_map.BIST)[0] or 0xFFFFFFFF     # not done: no result to report

    def load(self, blocks, bist=True, verify=True):
        """The whole load. Returns the COMMIT CRC; leaves the chip in load mode."""
        if bist:
            res = self.selftest()
            if res:
                raise FallbackNeeded("SRAM self-test failed (BIST 0x%08x)" % res)
        else:
            self.enter_load()
            self.link.exchange(frames.prologue(False))
        return self.write(blocks, verify)

    def boot(self, blocks, fallback, verify=True):
        """Self-test, then the SRAM image on a pass or the fallback image on a fail.
        Returns {"path": "sram" or "fallback", "crc": COMMIT CRC, "bist": 0 or
        the BIST register}; leaves the chip in load mode."""
        image.check_fallback(fallback)
        res = self.selftest()
        path = "fallback" if res else "sram"
        crc = self.write(fallback if res else blocks, verify)
        return {"path": path, "crc": crc, "bist": res}

    def write(self, blocks, verify=True):
        """Bursts, COMMIT, STAT and readback after BEGIN (and BIST). Returns the CRC."""
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
