"""Runtime SPI transactions: encoder, decoder and event-record helpers.

One transaction per CS_N low period, SPI mode 0, MSB first, carrying the
loader's command bytes:
    WR   0x01 addr(2) data(4)                  nothing back
    RD   0x02 addr(2) dummy        then 4 bytes back
    WRB  0x03 addr(2) n(2) data(4n)            nothing back
    RDB  0x04 addr(2) n(2) dummy   then 4n bytes back
    STAT 0x13                      then 1 byte back
The dummy byte gives the chip time to fetch over its host bus. A transaction
is (mosi bytes, number of bytes to clock in afterwards); the host clocks
zeros while reading. BEGIN, BIST and COMMIT are refused by the chip unless
every lane is stopped; encode them with control().

Portable: CPython 3 and MicroPython.
"""
from .frames import WR, RD, WRB, RDB, STAT, BEGIN, BIST, COMMIT, be16, be32, words_bytes

DUMMY = 0x00
EV_TRIG, EV_TIME = 0x3E, 0x3F


def _check(addr, count=1):
    if not 0 <= addr <= 0xFFFF or not 0 <= count <= 0xFFFF or addr + count > 0x10000:
        raise ValueError("address range 0x%x + %d" % (addr, count))


def write(addr, value):
    _check(addr)
    return bytes((WR,)) + be16(addr) + be32(value), 0


def read(addr):
    _check(addr)
    return bytes((RD,)) + be16(addr) + bytes((DUMMY,)), 4


def write_block(addr, words):
    _check(addr, len(words))
    return bytes((WRB,)) + be16(addr) + be16(len(words)) + words_bytes(words), 0


def read_block(addr, count):
    _check(addr, count)
    return bytes((RDB,)) + be16(addr) + be16(count) + bytes((DUMMY,)), 4 * count


def status():
    return bytes((STAT,)), 1


def control(code, payload=b""):
    if code not in (BEGIN, BIST, COMMIT):
        raise ValueError("not a control command: 0x%02x" % code)
    return bytes((code,)) + payload, 0


def words(reply):
    """32-bit words from reply bytes, MSB first."""
    if len(reply) % 4:
        raise ValueError("reply of %d bytes is not whole words" % len(reply))
    return [(reply[i] << 24) | (reply[i + 1] << 16) | (reply[i + 2] << 8) | reply[i + 3]
            for i in range(0, len(reply), 4)]


def parse(mosi):
    """Decode a host transaction: (code, addr, count, data words). Raises ValueError."""
    mosi = bytes(mosi)
    if not mosi:
        raise ValueError("empty transaction")
    code = mosi[0]
    if code in (STAT, BEGIN, BIST):
        if len(mosi) != 1:
            raise ValueError("0x%02x takes no payload" % code)
        return code, None, 0, []
    if code == COMMIT:
        if len(mosi) != 5:
            raise ValueError("COMMIT takes 4 bytes")
        return code, None, 0, words(mosi[1:])
    if code not in (WR, RD, WRB, RDB) or len(mosi) < 3:
        raise ValueError("bad transaction")
    addr = (mosi[1] << 8) | mosi[2]
    if code == WR:
        if len(mosi) != 7:
            raise ValueError("WR takes 6 bytes")
        return code, addr, 1, words(mosi[3:])
    if code == RD:
        if len(mosi) != 4:
            raise ValueError("RD takes address and a dummy byte")
        return code, addr, 1, []
    if len(mosi) < 5:
        raise ValueError("burst without a count")
    n = (mosi[3] << 8) | mosi[4]
    if code == WRB:
        if len(mosi) != 5 + 4 * n:
            raise ValueError("WRB of %d words carries %d data bytes" % (n, len(mosi) - 5))
        return code, addr, n, words(mosi[5:])
    if len(mosi) != 6:
        raise ValueError("RDB takes address, count and a dummy byte")
    return code, addr, n, []


def field(value, hi, lo):
    return (value >> lo) & ((1 << (hi - lo + 1)) - 1)


def decode_reg(entry, value):
    """{field: value} for a regs_map entry."""
    out = {}
    for name, (hi, lo) in entry["fields"].items():
        out[name] = field(value, hi, lo)
    return out


def event(record):
    """(lane, code, data, ts16) of a 32-bit event record."""
    return record >> 30, (record >> 24) & 0x3F, (record >> 16) & 0xFF, record & 0xFFFF


def timeline(records, epoch=0):
    """Rebuild full timestamps: a TIME record carries timestamp bits 31:16 for the
    records after it. Returns ([(ts32, lane, code, data)], epoch) with TIME records
    consumed; pass the returned epoch to the next call."""
    out = []
    for r in records:
        lane, code, data, ts = event(r)
        if code == EV_TIME:
            epoch = ts
            continue
        out.append(((epoch << 16) | ts, lane, code, data))
    return out, epoch
