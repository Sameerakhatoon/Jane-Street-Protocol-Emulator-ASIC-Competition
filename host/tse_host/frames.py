# SPDX-License-Identifier: Apache-2.0
"""Load-mode command streams for the TSE byte-bus loader.

A stream is a list of (byte, frame) pairs, one per strobe edge: frame = 1
marks a command byte, 0 a payload byte or a bare read strobe. Every
multi-byte field is most significant byte first. The running CRC-32 the
chip checks at COMMIT covers the address and data bytes of WR and WRB since
BEGIN, in order; command bytes and burst counts are not covered.

Portable: runs unchanged on CPython 3 and MicroPython (no f-strings,
dataclasses, typing or zlib).
"""
WR, RD, WRB, RDB = 0x01, 0x02, 0x03, 0x04
BEGIN, BIST, COMMIT, STAT = 0x10, 0x11, 0x12, 0x13
NAMES = {WR: "WR", RD: "RD", WRB: "WRB", RDB: "RDB",
         BEGIN: "BEGIN", BIST: "BIST", COMMIT: "COMMIT", STAT: "STAT"}

CHUNK = 64              # words per WRB burst: a retry after a bad readback costs little
MAX_COUNT = 0xFFFF
BARE = (0, 0)           # a read strobe: data ignored by the chip


def _table():
    t = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ (0xEDB88320 if c & 1 else 0)
        t.append(c)
    return t


_CRC_TABLE = _table()


def crc32_update(reg, data):
    """Advance the raw CRC register (no final XOR), as the chip does per byte."""
    for b in data:
        reg = (reg >> 8) ^ _CRC_TABLE[(reg ^ b) & 0xFF]
    return reg


def crc32(data, crc=0):
    """CRC-32/ISO-HDLC, equal to zlib.crc32(data, crc)."""
    return crc32_update(crc ^ 0xFFFFFFFF, data) ^ 0xFFFFFFFF


def be16(v):
    return bytes(((v >> 8) & 0xFF, v & 0xFF))


def be32(v):
    return bytes(((v >> 24) & 0xFF, (v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF))


def words_bytes(words):
    return b"".join([be32(w) for w in words])


def cmd(code, payload=b""):
    """The command byte with frame = 1, then its payload bytes with frame = 0."""
    return [(code, 1)] + [(b, 0) for b in payload]


def _check_addr(addr, count):
    if not 0 <= addr <= 0xFFFF or addr + count > 0x10000:
        raise ValueError("address range 0x%x + %d leaves the 16-bit host bus" % (addr, count))


def wr(addr, value):
    _check_addr(addr, 1)
    return cmd(WR, be16(addr) + be32(value))


def rd(addr):
    """RD then 4 bare strobes; the byte seen after strobe k is data byte k (MSB first)."""
    _check_addr(addr, 1)
    return cmd(RD, be16(addr)) + [BARE] * 4


def wrb(addr, words):
    if not 0 <= len(words) <= MAX_COUNT:
        raise ValueError("burst of %d words" % len(words))
    _check_addr(addr, len(words))
    return cmd(WRB, be16(addr) + be16(len(words)) + words_bytes(words))


def rdb(addr, count):
    """RDB then 4 x count bare strobes, each presenting the next byte (MSB first)."""
    if not 0 <= count <= MAX_COUNT:
        raise ValueError("burst of %d words" % count)
    _check_addr(addr, count)
    return cmd(RDB, be16(addr) + be16(count)) + [BARE] * (4 * count)


def stat():
    """STAT then one bare strobe; the status byte is seen after that strobe."""
    return cmd(STAT) + [BARE]


def begin():
    return cmd(BEGIN)


def bist():
    return cmd(BIST)


def commit(crc):
    return cmd(COMMIT, be32(crc))


def covered(stream):
    """The bytes of a stream the running CRC covers (address and data of WR and WRB)."""
    out = bytearray()
    code, pos = None, 0
    for byte, frame in stream:
        if frame:
            code, pos = byte, 0
            continue
        if code == WR or (code == WRB and pos != 2 and pos != 3):
            out.append(byte)
        pos += 1
    return bytes(out)


def chunks(blocks, chunk=CHUNK):
    """Split (addr, words) blocks into (addr, words) bursts of at most chunk words."""
    out = []
    for addr, words in blocks:
        for i in range(0, len(words), chunk):
            out.append((addr + i, words[i:i + chunk]))
    return out


def load_body(blocks, chunk=CHUNK):
    """WRB bursts for the blocks and the CRC COMMIT must carry. Returns (stream, crc)."""
    stream, reg = [], 0xFFFFFFFF
    for addr, words in chunks(blocks, chunk):
        stream += wrb(addr, words)
        reg = crc32_update(reg, be16(addr) + words_bytes(words))
    return stream, reg ^ 0xFFFFFFFF


def load_stream(blocks, bist_first=False, chunk=CHUNK):
    """BEGIN, optional BIST, the bursts, COMMIT. Returns (stream, crc).
    With BIST the host must wait for the self-test between BIST and the first
    burst, so a real session sends prologue() and load_body() separately."""
    body, crc = load_body(blocks, chunk)
    return prologue(bist_first) + body + commit(crc), crc


def prologue(with_bist=True):
    return begin() + (bist() if with_bist else [])


def readback(blocks, chunk=CHUNK):
    """RDB streams for the blocks, with the slice of seen bytes that hold each burst.
    Returns (stream, [(addr, count, first seen index)])."""
    stream, where = [], []
    for addr, words in chunks(blocks, chunk):
        where.append((addr, len(words), len(stream) + 5))
        stream += rdb(addr, len(words))
    return stream, where


def words_from(seen, first, count):
    """Rebuild count words from the bytes seen after strobes first, first + 1, ..."""
    out = []
    for i in range(count):
        b = seen[first + 4 * i:first + 4 * i + 4]
        out.append((b[0] << 24) | (b[1] << 16) | (b[2] << 8) | b[3])
    return out


def decode_stat(byte):
    """STAT byte = chip STATUS[7:0]."""
    return {
        "loaded": byte & 1,
        "bist_done": (byte >> 1) & 1,
        "bist_pass": (byte >> 2) & 1,
        "host_err": (byte >> 3) & 1,
        "halted": (byte >> 4) & 0xF,
    }


def describe(stream):
    """One line per command of a stream, for logs and the command line."""
    lines, i = [], 0
    while i < len(stream):
        code = stream[i][0] if stream[i][1] else None
        j = i + 1
        while j < len(stream) and not stream[j][1]:
            j += 1
        if code is None:
            lines.append("%d stray byte(s)" % (j - i))
        else:
            name = NAMES.get(code, "0x%02X" % code)
            body = [b for b, _ in stream[i + 1:j]]
            if code in (WR, RD, WRB, RDB) and len(body) >= 2:
                name += " 0x%04X" % ((body[0] << 8) | body[1])
            if code in (WRB, RDB) and len(body) >= 4:
                name += " n=%d" % ((body[2] << 8) | body[3])
            lines.append(name)
        i = j
    return lines
