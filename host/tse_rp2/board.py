"""Select, self-test, load, verify and run TSE on the Tiny Tapeout demo board.

The stock Tiny Tapeout SDK selects the project, sets the clock and drives
rst_n (GPIO14); our PIO programs drive the byte bus; hardware SPI0 carries
runtime access. BoardLink gives tse_host.session.Session the same five link
operations the simulation provides, so the board runs the exact load
sequence the tests run.

Order matters: clock_project_PWM() moves the RP2350 system clock (60 MHz
needs 120 MHz), which re-times PIO and SPI, so both are created after it.
"""
import rp2
from machine import Pin, freq

from tse_host import frames, image, session
from tse_rp2 import bytebus
from tse_rp2.port import sleep_us, ticks_ms, ticks_diff
from tse_rp2.regs import TseRegs

PROJECT = "tt_um_tse"
UI_PINS = range(17, 25)        # ui_in[0..7]
LOAD_UIO_PINS = (25, 26)       # uio[0] strobe, uio[1] frame: the crossbar's after loading
RESET_HIGH_US = 1              # 60 clocks at 60 MHz: rst_n high before load mode, and settling after


class BoardLink:
    def __init__(self, tt):
        self.tt = tt
        self.clock_hz = 0
        self.ent = bytebus.Entries()
        self.sm_tx = self.sm_rx = None
        self.regs = None

    def start_clock(self, clock_hz):
        self.tt.clock_project_PWM(clock_hz)
        self.clock_hz = clock_hz

    def enter_load(self):
        # high first: load mode starts on the synchronized falling edge,
        # which is what clears loaded on a freshly powered tile
        self.tt.reset_project(False)
        sleep_us(RESET_HIGH_US)
        self.tt.reset_project(True)
        sleep_us(RESET_HIGH_US)         # load mode is on before the first strobe edge
        rp2.PIO(0).gpio_base(Pin(16))
        hz = bytebus.pio_freq(freq())
        self.sm_tx = rp2.StateMachine(0, bytebus.loader_tx, freq=hz,
                                      out_base=Pin(bytebus.OUT_BASE))
        self.sm_rx = rp2.StateMachine(1, bytebus.loader_rx, freq=hz,
                                      out_base=Pin(bytebus.OUT_BASE), in_base=Pin(bytebus.IN_BASE))
        self.pio_hz = hz
        self.sm_tx.active(1)

    def leave_load(self):
        self.sm_tx.active(0)
        for n in UI_PINS:
            Pin(n, Pin.OUT, value=1 if n == 17 else 0)     # CS_N idle high
        for n in LOAD_UIO_PINS:
            Pin(n, Pin.IN)
        self.tt.reset_project(False)
        self.regs = TseRegs()

    def _put(self, sm, words):
        for w in words:
            sm.put(w)

    def send(self, stream):
        self._put(self.sm_tx, self.ent.words(stream, 2))
        while self.sm_tx.tx_fifo():
            pass
        sleep_us(2 + 2 * bytebus.TX_CYCLES * 1000000 // self.pio_hz)    # last word shifts out

    def exchange(self, stream):
        """Write-only streams go through loader_tx (fast); others through loader_rx,
        one RX word per four entries, collected as they come so neither FIFO fills."""
        if not stream_reads(stream):
            self.send(stream)
            return [0] * len(stream)
        words = self.ent.words(stream, 4)
        self.sm_tx.active(0)
        self.sm_rx.active(1)
        got = []
        for i, w in enumerate(words):
            self.sm_rx.put(w)
            if i % 2:
                got.append(self.sm_rx.get())
        self.sm_rx.active(0)
        self.sm_tx.active(1)
        return list(bytebus.rx_bytes(got, len(stream)))

    def wait(self, cycles):
        sleep_us(cycles * 1000000 // self.clock_hz + 1)

    def spi(self, mosi, nread):
        return self.regs.xfer(mosi, nread)


def stream_reads(stream):
    """True if a stream holds a command whose bytes come back (RD, RDB, STAT)."""
    for b, f in stream:
        if f and b in (frames.RD, frames.RDB, frames.STAT):
            return True
    return False


class TseBoard:
    def __init__(self, tt=None, project=PROJECT):
        if tt is None:
            from ttboard.demoboard import DemoBoard
            tt = DemoBoard.get()
        self.tt = tt
        self.project = project
        self.link = BoardLink(tt)
        self.session = session.Session(self.link)

    def select(self):
        getattr(self.tt.shuttle, self.project).enable()

    def load_image(self, path, verify=True, run_mask=None):
        """Select, clock, self-test, load, verify; then run mode and RUN if run_mask.
        Raises session.FallbackNeeded when the SRAM self-test fails."""
        with open(path, "rb") as f:
            name, clock_hz, blocks = image.unpack(f.read())
        t0 = ticks_ms()
        self.select()
        self.link.start_clock(clock_hz)
        crc = self.session.load(blocks, bist=True, verify=verify)
        st = None
        if run_mask is not None:
            st = self.session.start(run_mask)
        return name, crc, st, ticks_diff(ticks_ms(), t0)
