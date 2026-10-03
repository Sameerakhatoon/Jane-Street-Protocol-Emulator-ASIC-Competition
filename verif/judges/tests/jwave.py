"""Waveform writer and bit-bangers for the judge tests.

Independent of the judges: nothing here imports the reader or a decoder.
Each generator follows the protocol definition directly with explicit
times in seconds, and can break one rule on purpose (a bad stop bit, a
stuff error, a short setup time) so the tests can check that the judge
notices. Wave.write emits a VCD with integer time stamps.
"""

UNITS = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "ps": 1e-12, "fs": 1e-15}


class Wave:
    def __init__(self, unit="ns", mult=1):
        self.unit = unit
        self.mult = mult
        self.step = mult * UNITS[unit]
        self.sigs = {}      # name -> [width, scope, [(t_units, value)]]

    def add(self, name, init, width=1, scope=None):
        self.sigs[name] = [width, scope, [(0, init)]]

    def set(self, name, t, v):
        self.sigs[name][2].append((int(round(t / self.step)), v))

    def end(self):
        return max(ch[-1][0] for _, _, ch in self.sigs.values()) * self.step

    def _val(self, width, v):
        if width == 1:
            return str(v)
        if isinstance(v, str):
            return "b" + v + " "
        return "b" + format(v, "b") + " "

    def write(self, path, spaced=False, tail=0.0, extra=""):
        codes = {}
        for k, name in enumerate(self.sigs):
            codes[name] = chr(33 + k % 90) * (1 + k // 90)
        out = ["$date today $end", "$version jwave $end", "$comment written by the judge tests $end"]
        out.append("$timescale %d%s%s $end" % (self.mult, " " if spaced else "", self.unit))
        out.append("$scope module top $end")
        for name, (width, scope, _) in self.sigs.items():
            if scope:
                out.append("$scope module %s $end" % scope)
            rng = " [%d:0]" % (width - 1) if width > 1 else ""
            out.append("$var wire %d %s %s%s $end" % (width, codes[name], name, rng))
            if scope:
                out.append("$upscope $end")
        out.append(extra)
        out.append("$upscope $end")
        out.append("$enddefinitions $end")
        out.append("#0")
        out.append("$dumpvars")
        events = []
        for name, (width, _, ch) in self.sigs.items():
            out.append(self._val(width, ch[0][1]) + codes[name])
            for n, (t, v) in enumerate(ch[1:]):
                events.append((t, n, codes[name], self._val(width, v)))
        out.append("$end")
        events.sort(key=lambda e: (e[0], e[1]))
        now = 0
        for t, n, code, val in events:
            if t != now:
                out.append("#%d" % t)
                now = t
            out.append(val + code)
        last = now + int(round(tail / self.step))
        if last > now:
            out.append("#%d" % last)
        with open(path, "w") as f:
            f.write("\n".join(out) + "\n")
        return path


# UART ----------------------------------------------------------------

def uart_bits(value, bits=8, parity="none", stop=1.0, msb_first=False, bad_stop=False, bad_parity=False):
    data = [(value >> (bits - 1 - k if msb_first else k)) & 1 for k in range(bits)]
    seq = [0] + data
    if parity != "none":
        ones = sum(data) & 1
        p = {"even": ones, "odd": ones ^ 1, "mark": 1, "space": 0}[parity]
        seq.append(p ^ int(bad_parity))
    return seq, (0 if bad_stop else 1)


def uart_tx(w, name, t, value, baud, bits=8, parity="none", stop=1.0, invert=False,
            msb_first=False, bad_stop=False, bad_parity=False, scale=1.0, first_bit=None):
    """Send one character starting at t; returns the time the stop bits end.
    scale stretches every bit (a baud error); first_bit overrides the start bit length."""
    T = scale / baud
    seq, stop_level = uart_bits(value, bits, parity, stop, msb_first, bad_stop, bad_parity)
    inv = int(invert)
    tk = t
    for k, b in enumerate(seq):
        w.set(name, tk, b ^ inv)
        tk += first_bit if (k == 0 and first_bit) else T
    w.set(name, tk, stop_level ^ inv)
    tk += stop * T
    if stop_level == 0:
        w.set(name, tk, 1 ^ inv)
    return tk


# SPI -----------------------------------------------------------------

def spi_xfer(w, t, mosi, miso, mode=0, period=1e-6, bits=8, lsb_first=False, cs=True,
             shift=0.0, cs_lead=None):
    """One chip-select frame. Data changes on the launch edge plus shift."""
    cpol, cpha = mode >> 1, mode & 1
    half = period / 2
    lead = half if cs_lead is None else cs_lead
    if cs:
        w.set("cs", t, 0)
    nbits = bits * max(len(mosi), len(miso))

    def bit(words, k):
        word, i = divmod(k, bits)
        if word >= len(words):
            return 0
        return (words[word] >> (i if lsb_first else bits - 1 - i)) & 1

    for k in range(nbits):
        t_lead = t + lead + k * period
        t_trail = t_lead + half
        w.set("clk", t_lead, 1 - cpol)
        w.set("clk", t_trail, cpol)
        if cpha:
            t_launch = t_lead               # CPHA=1: launch on the leading edge
        else:
            t_launch = t if k == 0 else t_trail - period    # previous trailing edge
        for name, words in (("mosi", mosi), ("miso", miso)):
            if words and name in w.sigs:
                w.set(name, t_launch + shift, bit(words, k))
    t_end = t + lead + nbits * period
    if cs:
        w.set("cs", t_end, 1)
    return t_end


# I2C -----------------------------------------------------------------

SM = dict(thd_sta=4.5e-6, tlow=5.0e-6, thigh=5.0e-6, tsu_sta=5.0e-6, thd_dat=0.3e-6,
          tsu_dat=None, tsu_sto=4.5e-6, tbuf=5.0e-6)
FM = dict(thd_sta=0.6e-6, tlow=1.3e-6, thigh=1.2e-6, tsu_sta=0.6e-6, thd_dat=0.1e-6,
          tsu_dat=None, tsu_sto=0.6e-6, tbuf=1.3e-6)


class I2c:
    """Bus-level I2C generator (the wired-AND result of both devices).

    tsu_dat=None changes SDA thd_dat after SCL falls; a number changes it
    that long before SCL rises instead."""

    def __init__(self, w, timing, t=10e-6):
        self.w = w
        self.p = dict(timing)
        self.t = t
        self.scl = 1
        self.sda = 1
        self.idle = True
        w.add("scl", 1)
        w.add("sda", 1)

    def _scl(self, v):
        self.w.set("scl", self.t, v)
        self.scl = v

    def _sda(self, v, t=None):
        if v != self.sda:
            self.w.set("sda", self.t if t is None else t, v)
            self.sda = v

    def start(self):
        p = self.p
        if self.idle:
            self.t += p["tbuf"]
        else:
            # repeated START: release SDA during the low phase, raise SCL
            self._sda(1, self.t + p["thd_dat"])
            self.t += p["tlow"]
            self._scl(1)
            self.t += p["tsu_sta"]
        self._sda(0)
        self.t += p["thd_sta"]
        self._scl(0)
        self.idle = False

    def bit(self, v, stretch=0.0, tsu=None, thd=None):
        p = self.p
        rise = self.t + p["tlow"] + stretch
        su = p["tsu_dat"] if tsu is None else tsu
        when = rise - su if su is not None else self.t + (p["thd_dat"] if thd is None else thd)
        self._sda(v, when)
        self.t = rise
        self._scl(1)
        self.t += p["thigh"]
        self._scl(0)

    def byte(self, b, ack=True, stretch=0.0, **kw):
        for k in range(8):
            self.bit((b >> (7 - k)) & 1, stretch if k == 0 else 0.0, **kw)
        self.bit(0 if ack else 1)

    def stop(self):
        p = self.p
        self._sda(0, self.t + p["thd_dat"])
        self.t += p["tlow"]
        self._scl(1)
        self.t += p["tsu_sto"]
        self._sda(1)
        self.idle = True

    def transfer(self, addr, write=(), read=(), stop=True):
        """Write bytes, then (with a repeated START) read bytes; the last read is NACKed."""
        self.start()
        if write or not read:
            self.byte(addr << 1)
            for b in write:
                self.byte(b)
            if read:
                self.start()
        if read:
            self.byte(addr << 1 | 1)
            for k, b in enumerate(read):
                self.byte(b, ack=k != len(read) - 1)
        if stop:
            self.stop()


# CAN -----------------------------------------------------------------

def crc15_div(bits):
    """CRC-15 by polynomial long division (remainder of M(x) * x^15 mod G(x))."""
    v = 0
    for b in bits:
        v = (v << 1) | b
    v <<= 15
    g = 0xC599
    for i in range(v.bit_length() - 1, 14, -1):
        if (v >> i) & 1:
            v ^= g << (i - 15)
    return v


def _bits(v, n):
    return [(v >> (n - 1 - k)) & 1 for k in range(n)]


def can_bits(ident, data=b"", ext=False, rtr=False, dlc=None, ack=True, flip_crc=False,
             stuff_error=False, bad_eof=False, drop_last_stuff=False):
    """Raw bus levels of one frame, SOF to the end of the intermission.
    drop_last_stuff leaves out a stuff bit due right after the last CRC bit."""
    dlc = len(data) if dlc is None else dlc
    if ext:
        f = [0] + _bits(ident >> 18, 11) + [1, 1] + _bits(ident & 0x3FFFF, 18) + [int(rtr), 0, 0]
    else:
        f = [0] + _bits(ident, 11) + [int(rtr), 0, 0]
    f += _bits(dlc, 4)
    if not rtr:
        for b in data:
            f += _bits(b, 8)
    crc = crc15_div(f) ^ (1 if flip_crc else 0)
    f += _bits(crc, 15)
    out, run, last, broken = [], 0, None, False
    for k, b in enumerate(f):
        out.append(b)
        run = run + 1 if b == last else 1
        last = b
        if run == 5:
            s = 1 - b
            if stuff_error and not broken:
                s, broken = b, True
            if not (drop_last_stuff and k == len(f) - 1):
                out.append(s)
            run, last = 1, s
    out += [1, 0 if ack else 1, 1] + [1] * 7 + [1] * 3
    if bad_eof:
        out[-6] = 0
    return out


def can_wave(w, name, t, levels, bitrate, scale=1.0):
    T = scale / bitrate
    for k, v in enumerate(levels):
        w.set(name, t + k * T, v)
    return t + len(levels) * T


# SWD -----------------------------------------------------------------

def swd_request(apndp, rnw, addr):
    a2, a3 = (addr >> 2) & 1, (addr >> 3) & 1
    par = (apndp + rnw + a2 + a3) & 1
    return [1, apndp, rnw, a2, a3, par, 0, 1]


def _lsb_bits(v, n):
    return [(v >> k) & 1 for k in range(n)]


def swd_op(apndp, rnw, addr, data=0, ack=1, bad_req_parity=False, bad_data_parity=False):
    """Cycles of one packet: ("h", v) host drives, ("t", v) target drives, ("z",) nobody."""
    req = swd_request(apndp, rnw, addr)
    if bad_req_parity:
        req[5] ^= 1
    cyc = [("h", b) for b in req] + [("z",)]
    if ack is None:
        cyc += [("z",)] * 3
    else:
        cyc += [("t", b) for b in _lsb_bits(ack, 3)]
    if ack != 1:
        return cyc + [("z",)]
    bits = _lsb_bits(data, 32)
    par = (sum(bits) & 1) ^ int(bad_data_parity)
    if rnw:
        return cyc + [("t", b) for b in bits] + [("t", par), ("z",)]
    return cyc + [("z",)] + [("h", b) for b in bits] + [("h", par)]


def swd_line_reset(n=52):
    return [("h", 1)] * n


def swd_jtag_to_swd():
    return [("h", b) for b in _lsb_bits(0xE79E, 16)]


def swd_idle(n=2):
    return [("h", 0)] * n


def swd_wave(w, cycles, t=1e-6, period=200e-9, target_delay=0.0):
    """Clock low then high per cycle; host data set at the start of the low
    phase, target data launched on the rising edge plus target_delay periods."""
    half = period / 2
    for n, c in enumerate(cycles):
        t0 = t + n * period
        w.set("swclk", t0, 0)
        w.set("swclk", t0 + half, 1)
        if c[0] == "h":
            w.set("swdio", t0, c[1])
        elif c[0] == "t":
            w.set("swdio", t0 + half + target_delay * period, c[1])
        else:
            w.set("swdio", t0 + half, "z")
    end = t + len(cycles) * period
    w.set("swclk", end, 0)
    return end


# PS/2 ----------------------------------------------------------------

def ps2_dev(w, t, value, half=40e-6, bad_parity=False, bad_stop=False, data_lag=None):
    """Device to host; returns the end time. data_lag sets DATA that long after the rising edge."""
    data = _lsb_bits(value, 8)
    par = (1 - sum(data) % 2) ^ int(bad_parity)
    seq = [0] + data + [par, 0 if bad_stop else 1]
    lag = half / 2 if data_lag is None else data_lag
    for k, b in enumerate(seq):
        fall = t + half + k * 2 * half
        w.set("data", fall - half + lag if k else fall - half / 2, b)
        w.set("clk", fall, 0)
        w.set("clk", fall + half, 1)
    end = t + half + 11 * 2 * half
    w.set("data", end, 1)
    return end


def ps2_host(w, t, value, half=40e-6, rts=120e-6, ack=True, bad_parity=False, data_at=0.5):
    """Host to device with the device's clocking and ACK; returns the end time.
    The host changes DATA data_at of the way through each CLK low phase."""
    w.set("clk", t, 0)
    w.set("data", t + rts - 10e-6, 0)
    r0 = t + rts
    w.set("clk", r0, 1)
    data = _lsb_bits(value, 8)
    par = (1 - sum(data) % 2) ^ int(bad_parity)
    seq = data + [par, 1]
    for k in range(1, 12):
        fall = r0 + half + (k - 1) * 2 * half
        w.set("clk", fall, 0)
        w.set("clk", fall + half, 1)
        if k <= 10:
            w.set("data", fall + half * data_at, seq[k - 1])
        if k == 10 and ack:
            w.set("data", fall + half + half / 2, 0)
    end = r0 + half + 10 * 2 * half + half
    w.set("data", end + half / 2, 1)
    return end + half


# JTAG ----------------------------------------------------------------

# the TAP state diagram as a table of (state, TMS) -> next state
_TAP = {}
for _s, _n0, _n1 in (
        ("TLR", "RTI", "TLR"), ("RTI", "RTI", "SDR"),
        ("SDR", "CDR", "SIR"), ("CDR", "SHDR", "E1DR"), ("SHDR", "SHDR", "E1DR"),
        ("E1DR", "PDR", "UDR"), ("PDR", "PDR", "E2DR"), ("E2DR", "SHDR", "UDR"),
        ("UDR", "RTI", "SDR"),
        ("SIR", "CIR", "TLR"), ("CIR", "SHIR", "E1IR"), ("SHIR", "SHIR", "E1IR"),
        ("E1IR", "PIR", "UIR"), ("PIR", "PIR", "E2IR"), ("E2IR", "SHIR", "UIR"),
        ("UIR", "RTI", "SDR")):
    _TAP[(_s, 0)] = _n0
    _TAP[(_s, 1)] = _n1


class JtagDev:
    """One device: 4-bit IR (capture 0001), IDCODE 0x1 and BYPASS 0xF, the
    identification register selected in Test-Logic-Reset."""

    def __init__(self, idcode=0x4BA00477, has_idcode=True):
        self.idcode = idcode
        self.has_idcode = has_idcode
        self.state = "TLR"
        self.ir = 0x1 if has_idcode else 0xF
        self.sr = []

    def _dr_bits(self):
        if self.ir == 0x1 and self.has_idcode:
            return _lsb_bits(self.idcode, 32)
        return [0]

    def clock(self, tms, tdi):
        """One TCK rise; returns the TDO level driven before it (None = Z)."""
        tdo = None
        if self.state in ("SHDR", "SHIR"):
            tdo = self.sr[0]
            self.sr = self.sr[1:] + [tdi]
        nxt = _TAP[(self.state, tms)]
        if nxt == "CDR":
            self.sr = self._dr_bits()
        elif nxt == "CIR":
            self.sr = [1, 0, 0, 0]
        elif nxt == "UIR":
            self.ir = sum(b << k for k, b in enumerate(self.sr[-4:]))
        elif nxt == "TLR":
            self.ir = 0x1 if self.has_idcode else 0xF
        self.state = nxt
        return tdo


def jtag_reset():
    return [(1, 0)] * 5 + [(0, 0)]


def jtag_scan(value, n, ir=False):
    """From Run-Test/Idle: one IR or DR scan of n bits, back to Run-Test/Idle."""
    seq = [(1, 0), (1, 0), (0, 0), (0, 0)] if ir else [(1, 0), (0, 0), (0, 0)]
    for k in range(n):
        seq.append((1 if k == n - 1 else 0, (value >> k) & 1))
    return seq + [(1, 0), (0, 0)]


def jtag_wave(w, cycles, devs=None, period=100e-9, t=1e-6, tdo_force=None):
    """TCK low then high per cycle; host and device change on the falling edge.
    devs is a chain (TDI into devs[0]); tdo_force maps a cycle to a TDO value."""
    half = period / 2
    devs = devs or []
    for n, (tms, tdi) in enumerate(cycles):
        t0 = t + n * period
        w.set("tck", t0, 0)
        w.set("tms", t0, tms)
        w.set("tdi", t0, tdi)
        bit = tdi
        out = None
        for d in devs:
            o = d.clock(tms, bit)
            bit = 0 if o is None else o
            out = o
        if tdo_force and n in tdo_force:
            out = tdo_force[n]
        if devs or tdo_force:
            w.set("tdo", t0, "z" if out is None else out)
        w.set("tck", t0 + half, 1)
    end = t + len(cycles) * period
    w.set("tck", end, 0)
    return end


# USB low speed -------------------------------------------------------

USB_LS = 1.5e6


def crc_div(bits, width, poly):
    """USB CRC by polynomial long division: the first bit is the highest
    power, the register starts at all ones,
    the remainder is inverted and sent highest power first. The all-ones
    start adds x^len(bits) times all ones to the shifted message."""
    v = 0
    for b in bits:
        v = (v << 1) | b
    v <<= width
    v ^= ((1 << width) - 1) << len(bits)
    g = (1 << width) | poly
    for i in range(v.bit_length() - 1, width - 1, -1):
        if (v >> i) & 1:
            v ^= g << (i - width)
    v ^= (1 << width) - 1
    return [(v >> (width - 1 - k)) & 1 for k in range(width)]


def usb_crc5_bits(bits):
    return crc_div(bits, 5, 0x05)


def usb_crc16_bits(bits):
    return crc_div(bits, 16, 0x8005)


USB_PID = {"OUT": 0x1, "IN": 0x9, "SOF": 0x5, "SETUP": 0xD, "DATA0": 0x3, "DATA1": 0xB,
           "ACK": 0x2, "NAK": 0xA, "STALL": 0xE}


def usb_packet_bits(name, addr=0, endp=0, frame=0, data=b"", bad_pid=False, flip_crc=False):
    """PID and fields, least significant bit first, before stuffing and SYNC."""
    pid = USB_PID[name]
    chk = (pid ^ 0xF) ^ (1 if bad_pid else 0)
    bits = _lsb_bits(pid, 4) + _lsb_bits(chk, 4)
    if name in ("OUT", "IN", "SETUP", "SOF"):
        f = _lsb_bits(frame, 11) if name == "SOF" else _lsb_bits(addr, 7) + _lsb_bits(endp, 4)
        crc = usb_crc5_bits(f)
    elif name.startswith("DATA"):
        f = [b for byte in data for b in _lsb_bits(byte, 8)]
        crc = usb_crc16_bits(f)
    else:
        return bits
    if flip_crc:
        crc[0] ^= 1
    return bits + f + crc


def usb_line(bits, stuff=True, stuff_error=False, skip_last_stuff=False):
    """SYNC plus bits -> list of 'J'/'K' symbols with bit stuffing and NRZI."""
    raw = [0, 0, 0, 0, 0, 0, 0, 1] + list(bits)
    out, run = [], 0
    broken = False
    for k, b in enumerate(raw):
        out.append(b)
        run = run + 1 if b else 0
        if run == 6 and stuff:
            last = k == len(raw) - 1
            if stuff_error and not broken:
                out.append(1)
                broken = True
            elif not (last and skip_last_stuff):
                out.append(0)
            run = 0
    sym, level = [], "J"
    for b in out:
        if b == 0:
            level = "K" if level == "J" else "J"
        sym.append(level)
    return sym


def usb_wave(w, t, symbols, se0_bits=2.0, scale=1.0, after_j=1.0, skew=0.0):
    """Drive J/K symbols then SE0 for se0_bits bit times then J; returns end time.
    Low speed: J is D+ low, D- high. skew delays every D- change."""
    T = scale / USB_LS
    lv = {"J": (0, 1), "K": (1, 0)}
    for k, s in enumerate(symbols):
        p, m = lv[s]
        w.set("dp", t + k * T, p)
        w.set("dm", t + k * T + skew, m)
    te = t + len(symbols) * T
    w.set("dp", te, 0)
    w.set("dm", te, 0)
    te += se0_bits * T
    w.set("dm", te, 1)
    return te + after_j * T


# Manchester and 10BASE-T ---------------------------------------------

def manchester_wave(w, name, t, bits, bitrate, convention="ieee", idle=0, drop=None, scale=1.0):
    """Each bit as two half cells; drop=k removes bit k's middle transition.
    Returns the time the line goes back to idle."""
    T = scale / bitrate
    for k, b in enumerate(bits):
        second = b if convention == "ieee" else 1 - b
        first = 1 - second
        if k == drop:
            first = second
        w.set(name, t + k * T, first)
        w.set(name, t + k * T + T / 2, second)
    end = t + len(bits) * T
    w.set(name, end, idle)
    return end


def eth_frame_bits(dst, src, etype, payload, bad_fcs=False, preamble=7, dribble=0):
    """Preamble, SFD, MAC frame and FCS as wire bits (zlib computes the FCS)."""
    import zlib
    body = bytes(dst) + bytes(src) + etype.to_bytes(2, "big") + bytes(payload)
    if len(body) < 60:
        body += bytes(60 - len(body))
    fcs = zlib.crc32(body) ^ (1 if bad_fcs else 0)
    octets = bytes([0x55] * preamble + [0xD5]) + body + fcs.to_bytes(4, "little")
    bits = [b for o in octets for b in _lsb_bits(o, 8)]
    return bits + [1] * dribble
