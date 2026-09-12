"""The TSE register maps as a Python API, on top of the generated regs_table.

LANE holds the lane registers (CSRW/CSRR addresses), CHIP the host map.
pack() and unpack() convert between field values and 32-bit words and apply
the table's limits and require rules, so a configuration generator cannot
emit pos = 255 or a tick delay in bit mode. Standard library only.
"""
from .regs_table import TABLE

LANE = TABLE["lane"]
CHIP = TABLE["chip"]
LANE_WINDOW = TABLE["lane_window"]
REGIONS = TABLE["regions"]
LANE_BANK_WORDS = TABLE["lane_bank_words"]
LANE_BY_ADDR = {r["addr"]: n for n, r in LANE.items()}


class RegError(ValueError):
    pass


def lane_reg(addr):
    """Name of the lane register at addr, or None."""
    return LANE_BY_ADDR.get(addr)


def is_cfg(addr):
    """True for a class-L (configuration) lane register: CSRW costs 3 cycles."""
    n = LANE_BY_ADDR.get(addr)
    return n is not None and LANE[n]["class"] == "L"


def lane_writable(addr):
    n = LANE_BY_ADDR.get(addr)
    return n is not None and LANE[n]["class"] in ("L", "F", "P")


def lane_readable(addr):
    n = LANE_BY_ADDR.get(addr)
    return n is not None and LANE[n]["class"] in ("F", "P", "R")


def host_addr(name, lane=None, index=0):
    """Host address of a lane register (give lane) or a chip register (index for arrays)."""
    if lane is not None:
        if not 0 <= lane < LANE_WINDOW["max_lanes"]:
            raise RegError(f"lane {lane} out of range")
        return LANE_WINDOW["host_base"] + LANE_WINDOW["stride"] * lane + LANE[name]["addr"]
    r = CHIP[name]
    if not 0 <= index < r["count"]:
        raise RegError(f"{name}[{index}] out of range")
    return r["addr"] + index * r["stride"]


def _reg(name):
    if name in LANE:
        return LANE[name]
    if name in CHIP:
        return CHIP[name]
    raise RegError(f"unknown register {name}")


def pack(name, **fields):
    """32-bit value of register name from field values (ints or enum names)."""
    r = _reg(name)
    word, vals = 0, {}
    for fname, v in fields.items():
        if fname not in r["fields"]:
            raise RegError(f"{name} has no field {fname}")
        f = r["fields"][fname]
        if isinstance(v, str):
            if v.upper() not in f.get("enum", {}):
                raise RegError(f"{name}.{fname} has no value named {v}")
            v = f["enum"][v.upper()]
        top = (1 << (f["hi"] - f["lo"] + 1)) - 1
        if not f.get("min", 0) <= v <= f.get("max", top):
            raise RegError(f"{name}.{fname}={v} outside {f.get('min', 0)} to {f.get('max', top)}")
        vals[fname] = v
        word |= v << f["lo"]
    for rule in r["require"]:
        if all(vals.get(k, 0) == v for k, v in rule["if"].items()):
            for k, v in rule["then"].items():
                if vals.get(k, 0) != v:
                    raise RegError(f"{name}: {k} must be {v} when "
                                   + ", ".join(f"{a}={b}" for a, b in rule["if"].items()))
    return word


def unpack(name, value):
    """Field values of register name; bits outside every field must be 0."""
    r = _reg(name)
    if value & ~r["mask"] & 0xFFFFFFFF:
        raise RegError(f"{name}: value 0x{value:08X} sets bits that do not exist")
    return {f: (value >> s["lo"]) & ((1 << (s["hi"] - s["lo"] + 1)) - 1)
            for f, s in r["fields"].items()}


def check(name, value):
    """Raise RegError unless value is a legal word for register name."""
    pack(name, **unpack(name, value))
