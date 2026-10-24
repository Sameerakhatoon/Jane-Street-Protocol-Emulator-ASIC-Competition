"""Select, self-test, load, verify and run TSE on the Tiny Tapeout demo board.

The stock Tiny Tapeout SDK selects the project, sets the clock and drives
rst_n (GPIO14); our PIO programs drive the byte bus; hardware SPI0 carries
runtime access. BoardLink gives tse_host.session.Session the same five link
operations the simulation provides, so the board runs the exact load
sequence the tests run.

Order matters: enable() puts the RP2350 back on its default system clock and
clock_project_PWM() then moves it (60 MHz needs 120 MHz), which re-times PIO
and SPI, so both are created after it.

reset_project(False) does not drive rst_n high: it turns GPIO14 into an input
and the board's pull-up (through the reset button's debounce network) raises
it, so every release waits until the pin reads high.
"""
import rp2
from machine import Pin, freq

from tse_host import frames, image, session
from tse_rp2 import bytebus
from tse_rp2.port import sleep_us, ticks_ms, ticks_diff
from tse_rp2.regs import TseRegs

PROJECT = "tt_um_tse_sk_chip"   # the shuttle index names projects by top module
UI_PINS = range(17, 25)        # ui_in[0..7]
LOAD_UIO_PINS = (25, 26)       # uio[0] strobe, uio[1] frame: the crossbar's after loading
PIO_BASE = 16                  # PIO window GPIO16..47 reaches ui_in, uio and uo_out
RESET_HIGH_US = 1              # 60 clocks at 60 MHz: rst_n high before load mode, and settling after
RESET_RISE_MS = 100            # the pull-up must win by then, or the button is held


class BoardLink:
    def __init__(self, tt):
        self.tt = tt
        self.clock_hz = 0
        self.ent = bytebus.Entries()
        self.sm_tx = self.sm_rx = None
        self.regs = None

    def start_clock(self, clock_hz):
        if self.tt.clock_project_PWM(clock_hz) is None:
            raise RuntimeError("project clock %d Hz did not start" % clock_hz)
        self.clock_hz = clock_hz

    def release_reset(self):
        self.tt.reset_project(False)
        t0 = ticks_ms()
        while not self.tt.rst_n():
            if ticks_diff(ticks_ms(), t0) > RESET_RISE_MS:
                raise RuntimeError("rst_n stays low after release")
        sleep_us(RESET_HIGH_US)

    def enter_load(self):
        # high first: load mode starts on the synchronized falling edge,
        # which is what clears loaded on a freshly powered tile
        self.release_reset()
        self.tt.reset_project(True)
        sleep_us(RESET_HIGH_US)         # load mode is on before the first strobe edge
        set_pio_window()
        hz = bytebus.pio_freq(freq())
        self.sm_tx = rp2.StateMachine(0, bytebus.loader_tx, freq=hz,
                                      out_base=Pin(bytebus.OUT_BASE))
        self.sm_rx = rp2.StateMachine(1, bytebus.loader_rx, freq=hz,
                                      out_base=Pin(bytebus.OUT_BASE), in_base=Pin(bytebus.IN_BASE))
        self.pio_hz = hz
        self.ent = bytebus.Entries()    # out_init has driven the strobe pin low
        self.sm_tx.active(1)

    def leave_load(self):
        self.sm_tx.active(0)
        for n in UI_PINS:
            Pin(n, Pin.OUT, value=1 if n == 17 else 0)     # CS_N idle high
        for n in LOAD_UIO_PINS:
            Pin(n, Pin.IN)
        self.release_reset()
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


def set_pio_window():
    """Move PIO0's GPIO window to 16..47. The RP2350 refuses the move while any
    program sits in PIO0's instruction memory, which after the first load holds
    our own two programs with the window already moved."""
    pio = rp2.PIO(0)
    if not hasattr(pio, "gpio_base"):
        raise RuntimeError("this MicroPython has no PIO.gpio_base (RP2350B support)")
    try:
        pio.gpio_base(Pin(PIO_BASE))
    except OSError:
        pass


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
        """ASIC_RP_CONTROL first, so enable() applies the project's config.ini
        section and the RP2350 drives ui_in, clock and reset."""
        from ttboard.mode import RPMode
        if self.tt.mode != RPMode.ASIC_RP_CONTROL:
            self.tt.mode = RPMode.ASIC_RP_CONTROL
        if not getattr(self.tt.shuttle, self.project).enable():
            raise RuntimeError("the SDK refused to enable %s" % self.project)

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

    def boot_image(self, path, fallback_path, verify=True, run_mask=None):
        """The power-up flow: self-test, then the SRAM image, or the fallback image
        when the self-test fails. Returns the session's boot report plus "name",
        "status" (after RUN, or None) and "ms"."""
        with open(path, "rb") as f:
            name, clock_hz, blocks = image.unpack(f.read())
        with open(fallback_path, "rb") as f:
            blob = f.read()
        if not image.is_fallback(blob):
            raise image.ImageError("%s is not a fallback image" % fallback_path)
        fb_name, fb_clock, fb_blocks = image.unpack(blob)
        if fb_clock != clock_hz:
            raise image.ImageError("images want %d Hz and %d Hz" % (clock_hz, fb_clock))
        t0 = ticks_ms()
        self.select()
        self.link.start_clock(clock_hz)
        rep = self.session.boot(blocks, fb_blocks, verify=verify)
        rep["name"] = fb_name if rep["path"] == "fallback" else name
        rep["status"] = None if run_mask is None else self.session.start(run_mask)
        rep["ms"] = ticks_diff(ticks_ms(), t0)
        return rep
