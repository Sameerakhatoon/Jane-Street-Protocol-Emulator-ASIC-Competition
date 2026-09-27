"""Event FIFO records: decode, encode, and full 32-bit timestamps.

A record popped from EV_POP is one word, high bits first:

    lane | code | data | ts

The widths come from the ISA and register tables: code is the EMIT code
field, lane covers the lane window, data is X[7:0] at the EMIT, and ts is
whatever is left of the EV_POP word (16 bits). The two codes above the
highest code a program may emit are the event unit's own: TRIG (data holds
stage information) and TIME, whose ts field carries timestamp bits [31:16]
for the records after it. A TIME record precedes the first record of each
new epoch, so the host rebuilds full timestamps by tracking the epoch.
"""
from tools.tseasm import isa, regs

_code = isa.INSTRS["EMIT"]["fields"]["code"]
_rec = regs.CHIP["EV_POP"]["fields"]["record"]

RECORD_BITS = _rec["hi"] - _rec["lo"] + 1
CODE_BITS = _code["hi"] - _code["lo"] + 1
CODE_MAX = _code["asm_max"]
TRIG = CODE_MAX + 1
TIME = CODE_MAX + 2
LANE_BITS = (regs.LANE_WINDOW["max_lanes"] - 1).bit_length()
DATA_BITS = 8
TS_BITS = RECORD_BITS - LANE_BITS - CODE_BITS - DATA_BITS
EPOCH = 1 << TS_BITS

DATA_SHIFT = TS_BITS
CODE_SHIFT = DATA_SHIFT + DATA_BITS
LANE_SHIFT = CODE_SHIFT + CODE_BITS


class Event:
    """One decoded record with its rebuilt timestamp (cycles since the capture began)."""
    __slots__ = ("ts", "lane", "code", "data")

    def __init__(self, ts, lane, code, data):
        self.ts, self.lane, self.code, self.data = ts, lane, code, data

    def __eq__(self, o):
        return isinstance(o, Event) and self.key() == o.key()

    def __repr__(self):
        return "Event(ts=%d, lane=%d, code=0x%02X, data=0x%02X)" % self.key()

    def key(self):
        return (self.ts, self.lane, self.code, self.data)


class RecordError(ValueError):
    pass


def split(word):
    """(lane, code, data, ts16) of one record word."""
    if not 0 <= word < 1 << RECORD_BITS:
        raise RecordError("record 0x%X is wider than %d bits" % (word, RECORD_BITS))
    m = lambda bits: (1 << bits) - 1  # noqa: E731
    return (word >> LANE_SHIFT, (word >> CODE_SHIFT) & m(CODE_BITS),
            (word >> DATA_SHIFT) & m(DATA_BITS), word & m(TS_BITS))


def pack(lane, code, data, ts16):
    for v, bits, what in ((lane, LANE_BITS, "lane"), (code, CODE_BITS, "code"),
                          (data, DATA_BITS, "data"), (ts16, TS_BITS, "ts")):
        if not 0 <= v < 1 << bits:
            raise RecordError("%s %d does not fit %d bits" % (what, v, bits))
    return lane << LANE_SHIFT | code << CODE_SHIFT | data << DATA_SHIFT | ts16


def decode(words, epoch=0):
    """Events of a record stream, TIME records consumed. Returns (events, epoch);
    pass epoch on to decode the next chunk of the same capture. The 32-bit
    counter wrapping (a TIME epoch going backwards) adds a full wrap, so ts
    keeps increasing over long captures."""
    events, hi = [], epoch
    for w in words:
        lane, code, data, ts = split(w)
        if code == TIME:
            nxt = hi - hi % EPOCH + ts
            hi = nxt if nxt >= hi else nxt + EPOCH
            continue
        events.append(Event(hi * EPOCH + ts, lane, code, data))
    return events, hi


def encode(events):
    """Record words for events in time order, with a TIME record before the
    first record of each new epoch (and before the first record of all)."""
    out, cur = [], None
    for e in events:
        hi = e.ts // EPOCH
        if hi != cur:
            if cur is not None and hi < cur:
                raise RecordError("events out of time order")
            out.append(pack(0, TIME, 0, hi % EPOCH))
            cur = hi
        out.append(pack(e.lane, e.code, e.data, e.ts % EPOCH))
    return out


def parse_words(text):
    """Record words from text: one hex word per line, optional 0x, '#' comments."""
    out = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        for tok in line.split():
            try:
                out.append(int(tok, 16))
            except ValueError:
                raise RecordError("line %d: %r is not a hex record" % (n, tok))
    return out
