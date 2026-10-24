"""TSEI images: what the board stores so it needs no compiler.

Format version 1, every field most significant byte first:
    "TSEI"  version (u8)  clock_hz (u32)  name_len (u8)  name
    block_count (u16)  then per block: addr (u16)  count (u16)  count x word (u32)
    CRC-32 (u32) over every byte before it
A block is a run of host-bus words: a program window, the data window, a
lane's configuration words or chip registers. build() turns assembled
programs plus configuration into blocks; regions() names what each block is.

The fallback variant has the magic "TSEF" and the same layout. It is what
the host loads when the SRAM self-test fails, so it must not touch the SRAM:
its program lives in the fallback store (8 words, 16 instructions, shared by
every lane that runs from it), every LANE_PROG it writes selects that store,
and it has no SRAM or data window blocks (the data window is SRAM window
3). check_fallback() states those rules; pack() and unpack() apply them.

Portable: CPython 3 and MicroPython.
"""
from .frames import be16, be32, crc32, words_bytes
from . import regs_map

MAGIC = b"TSEI"
MAGIC_FALLBACK = b"TSEF"
VERSION = 1
HEADER = 4 + 1 + 4 + 1          # magic, version, clock, name length
WINDOW_WORDS = 0x80


class ImageError(ValueError):
    pass


def _check_blocks(blocks):
    spans = []
    for addr, words in blocks:
        if not 0 <= addr <= 0xFFFF or not 0 < len(words) <= 0xFFFF or addr + len(words) > 0x10000:
            raise ImageError("block 0x%x + %d words does not fit the host bus" % (addr, len(words)))
        for w in words:
            if not 0 <= w <= 0xFFFFFFFF:
                raise ImageError("word 0x%x in block 0x%04x is not 32 bits" % (w, addr))
        spans.append((addr, addr + len(words)))
    spans.sort()
    for i in range(1, len(spans)):
        if spans[i][0] < spans[i - 1][1]:
            raise ImageError("blocks overlap at 0x%04x" % spans[i][0])


def _in(addr, n, region):
    r = regs_map.REGIONS[region]
    return addr < r["base"] + r["size"] and r["base"] < addr + n


def check_fallback(blocks):
    """Raise ImageError unless blocks form a valid fallback image."""
    fb = regs_map.REGIONS["fallback"]
    prog = regs_map.LANE["LANE_PROG"]
    w = regs_map.LANE_WINDOW
    for addr, words in blocks:
        n = len(words)
        for region in ("sram", "data"):
            if _in(addr, n, region):
                raise ImageError("fallback image writes the %s at 0x%04x" % (region, addr))
        if _in(addr, n, "fallback") and not (fb["base"] <= addr and addr + n <= fb["base"] + fb["size"]):
            raise ImageError("block 0x%04x + %d words overruns the fallback store (%d words)"
                             % (addr, n, fb["size"]))
        for i in range(n):
            a = addr + i
            if w["host_base"] <= a < w["host_base"] + w["stride"] * w["max_lanes"] \
                    and (a - w["host_base"]) % w["stride"] == prog["addr"]:
                if not (words[i] >> prog["fields"]["fallback"][1]) & 1:
                    raise ImageError("lane %d LANE_PROG does not select the fallback store"
                                     % ((a - w["host_base"]) // w["stride"]))


def pack(blocks, clock_hz, name=b"", fallback=False):
    """Image bytes for blocks [(addr, [words])]; fallback=True writes the TSEF variant."""
    if isinstance(name, str):
        name = name.encode()
    if len(name) > 255:
        raise ImageError("name longer than 255 bytes")
    if not 0 < clock_hz <= 0xFFFFFFFF:
        raise ImageError("clock %r Hz" % (clock_hz,))
    if len(blocks) > 0xFFFF:
        raise ImageError("too many blocks")
    _check_blocks(blocks)
    if fallback:
        check_fallback(blocks)
    out = bytearray(MAGIC_FALLBACK if fallback else MAGIC)
    out += bytes((VERSION,)) + be32(clock_hz) + bytes((len(name),)) + name
    out += be16(len(blocks))
    for addr, words in blocks:
        out += be16(addr) + be16(len(words)) + words_bytes(words)
    return bytes(out + be32(crc32(out)))


def _u16(b, p):
    return (b[p] << 8) | b[p + 1]


def _u32(b, p):
    return (b[p] << 24) | (b[p + 1] << 16) | (b[p + 2] << 8) | b[p + 3]


def unpack(blob):
    """(name, clock_hz, blocks) from image bytes of either variant; raises
    ImageError on any defect."""
    blob = bytes(blob)
    if len(blob) < HEADER + 2 + 4 or blob[:4] not in (MAGIC, MAGIC_FALLBACK):
        raise ImageError("not a TSEI image")
    if blob[4] != VERSION:
        raise ImageError("TSEI version %d, expected %d" % (blob[4], VERSION))
    if crc32(blob[:-4]) != _u32(blob, len(blob) - 4):
        raise ImageError("image CRC mismatch")
    end = len(blob) - 4
    clock_hz = _u32(blob, 5)
    nlen = blob[9]
    pos = HEADER + nlen
    if pos + 2 > end:
        raise ImageError("truncated header")
    name = blob[HEADER:pos]
    nblocks = _u16(blob, pos)
    pos += 2
    blocks = []
    for _ in range(nblocks):
        if pos + 4 > end:
            raise ImageError("truncated block header")
        addr, n = _u16(blob, pos), _u16(blob, pos + 2)
        pos += 4
        if pos + 4 * n > end:
            raise ImageError("truncated block at 0x%04x" % addr)
        blocks.append((addr, [_u32(blob, pos + 4 * i) for i in range(n)]))
        pos += 4 * n
    if pos != end:
        raise ImageError("%d stray bytes after the blocks" % (end - pos))
    _check_blocks(blocks)
    if blob[:4] == MAGIC_FALLBACK:
        check_fallback(blocks)
    return name, clock_hz, blocks


def is_fallback(blob):
    return bytes(blob[:4]) == MAGIC_FALLBACK


def crc_of(blob):
    return _u32(blob, len(blob) - 4)


def lane_addr(lane, reg):
    w = regs_map.LANE_WINDOW
    if not 0 <= lane < w["max_lanes"]:
        raise ImageError("lane %d out of range" % lane)
    return w["host_base"] + w["stride"] * lane + regs_map.LANE[reg]["addr"]


def chip_addr(reg, index=0):
    r = regs_map.CHIP[reg]
    if not 0 <= index < r.get("count", 1):
        raise ImageError("%s[%d] out of range" % (reg, index))
    return r["addr"] + index * r.get("stride", 1)


def field(reg_entry, **values):
    """Pack field values into a register word (no range rules: the PC tools check those)."""
    word = 0
    for k, v in values.items():
        hi, lo = reg_entry["fields"][k]
        if not 0 <= v < (1 << (hi - lo + 1)):
            raise ImageError("field %s=%d does not fit" % (k, v))
        word |= v << lo
    return word


def sram_words(insns, pad):
    """Two 16-bit instructions per SRAM word, the even address in bits 15:0."""
    ws = list(insns) + ([pad] if len(insns) % 2 else [])
    return [ws[i] | (ws[i + 1] << 16) for i in range(0, len(ws), 2)]


def merge(writes):
    """[(addr, word)] to the fewest contiguous blocks; a later write to an address wins."""
    last = {}
    for a, w in writes:
        last[a] = w
    blocks = []
    for a in sorted(last):
        if blocks and blocks[-1][0] + len(blocks[-1][1]) == a:
            blocks[-1][1].append(last[a])
        else:
            blocks.append((a, [last[a]]))
    return blocks


def build(lanes, chip=(), data=None, pad=0xE000):
    """Blocks for an image.
    lanes: {lane: program}, program with attributes window, start, words (16-bit
    instructions) and config [(lane register, value)]; LANE_PROG is written
    from window and start. chip: [(chip register, index, value)].
    data: words for the data window. pad fills an odd instruction count (DLY 0)."""
    writes = []
    used = {}
    for lane in sorted(lanes):
        p = lanes[lane]
        if p.window in used:
            raise ImageError("lanes %d and %d share window %d" % (used[p.window], lane, p.window))
        used[p.window] = lane
        packed = sram_words(p.words, pad)
        if len(packed) > WINDOW_WORDS:
            raise ImageError("lane %d program is %d words, a window holds %d"
                             % (lane, len(packed), WINDOW_WORDS))
        base = regs_map.REGIONS["sram"]["base"] + WINDOW_WORDS * p.window
        writes += [(base + i, w) for i, w in enumerate(packed)]
        prog = regs_map.LANE["LANE_PROG"]
        writes.append((lane_addr(lane, "LANE_PROG"), field(prog, start_pc=p.start, window=p.window)))
        writes += [(lane_addr(lane, reg), v) for reg, v in p.config]
    for reg, index, v in chip:
        writes.append((chip_addr(reg, index), v))
    if data:
        if len(data) > regs_map.REGIONS["data"]["size"]:
            raise ImageError("data window holds %d words" % regs_map.REGIONS["data"]["size"])
        writes += [(regs_map.REGIONS["data"]["base"] + i, w) for i, w in enumerate(data)]
    return merge(writes)


def build_fallback(lanes, chip=(), pad=0xE000):
    """Blocks for a fallback image: the same arguments as build() without data.
    Every lane runs from the fallback store, which holds one program of at
    most 16 instructions (a lane fetches word PC[3:1]), so lanes given more
    than one program must give the same one."""
    writes = []
    words = None
    size = regs_map.REGIONS["fallback"]["size"]
    prog = regs_map.LANE["LANE_PROG"]
    for lane in sorted(lanes):
        p = lanes[lane]
        packed = sram_words(p.words, pad)
        if len(packed) > size:
            raise ImageError("lane %d program is %d instructions, the fallback store holds %d"
                             % (lane, len(p.words), 2 * size))
        if not 0 <= p.start < 2 * size:
            raise ImageError("lane %d starts at %d, outside the fallback store" % (lane, p.start))
        if words is not None and packed != words:
            raise ImageError("lanes running from the fallback store must share one program")
        words = packed
        writes.append((lane_addr(lane, "LANE_PROG"), field(prog, start_pc=p.start, fallback=1)))
        writes += [(lane_addr(lane, reg), v) for reg, v in p.config]
    if words:
        base = regs_map.REGIONS["fallback"]["base"]
        writes += [(base + i, w) for i, w in enumerate(words)]
    for reg, index, v in chip:
        writes.append((chip_addr(reg, index), v))
    blocks = merge(writes)
    check_fallback(blocks)
    return blocks


def region_of(addr):
    for name, r in regs_map.REGIONS.items():
        if r["base"] <= addr < r["base"] + r["size"]:
            return name
    return None


def describe_block(addr, n):
    """Human name of a block: 'sram window 1', 'lane 1 registers', 'chip', ..."""
    reg = region_of(addr)
    if reg == "sram":
        return "sram window %d" % (addr // WINDOW_WORDS)
    if reg == "lanes":
        w = regs_map.LANE_WINDOW
        return "lane %d registers" % ((addr - w["host_base"]) // w["stride"])
    return reg or "unmapped"


def show(blob):
    """Text summary of an image."""
    name, clock_hz, blocks = unpack(blob)
    lines = ["kind   %s" % ("fallback" if is_fallback(blob) else "sram"),
             "name   %s" % name.decode(),
             "clock  %d Hz" % clock_hz,
             "crc    0x%08X" % crc_of(blob),
             "bytes  %d" % len(blob),
             "blocks %d" % len(blocks)]
    for addr, words in blocks:
        lines.append("  0x%04X %4d words  %s" % (addr, len(words), describe_block(addr, len(words))))
    return "\n".join(lines)
