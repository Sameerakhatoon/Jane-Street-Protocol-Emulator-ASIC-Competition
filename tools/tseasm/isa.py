# SPDX-License-Identifier: Apache-2.0
"""The TSE lane ISA as a Python API, on top of the generated isa_table.

Stable interface for the assembler, the compiler (tools/tsec) and the
reference model (model/). Field values are logical: enums as their numbers
(names accepted by encode), minus1 fields as the written count (1 to 32).
Every instruction also has the side-set field ss. Standard library only.
"""
from typing import NamedTuple

from . import regs
from .isa_table import TABLE

VERSION = TABLE["version"]
WORD_BITS = TABLE["word_bits"]
FRAME_SLOTS = TABLE["frame_slots"]
FETCH_ENTRIES = TABLE["fetch_entries"]
FETCH_LATENCY = TABLE["fetch_latency"]
DATA_FRAME = TABLE["data_frame"]
LATENCY = TABLE["latency"]
ENUMS = TABLE["enums"]
WAIT_IDX = TABLE["wait_idx"]
INSTRS = TABLE["instrs"]
SS = TABLE["common"]["ss"]


class IsaError(ValueError):
    pass


class Instr(NamedTuple):
    name: str
    fields: dict


class Cost(NamedTuple):
    base: int          # cycles before waiting, data slots and fetch stalls
    wait: bool         # may stall on a condition or a mailbox (WAIT, blocking PUSH/PULL)
    dslot: object      # None, "load" or "store": waits for the lane's data slot


def _get(word, hi, lo):
    return (word >> lo) & ((1 << (hi - lo + 1)) - 1)


def field_range(name, fname):
    """Logical (low, high) range the encoder accepts for one field."""
    if fname == "ss":
        return 0, (1 << (SS[0] - SS[1] + 1)) - 1
    f = INSTRS[name]["fields"][fname]
    top = (1 << (f["hi"] - f["lo"] + 1)) - 1
    if f.get("kind") == "minus1":
        return 1, top + 1
    return 0, top


def _raw(name, fname, v):
    f = INSTRS[name]["fields"][fname]
    if isinstance(v, str):
        names = ENUMS[f["enum"]] if "enum" in f else None
        if names is None or v.upper() not in names:
            raise IsaError(f"{name}: {fname} has no value named {v}")
        v = names[v.upper()]
    lo, hi = field_range(name, fname)
    if not lo <= v <= hi:
        raise IsaError(f"{name}: {fname}={v} outside {lo} to {hi}")
    if "enum" in f and v not in ENUMS[f["enum"]].values():
        raise IsaError(f"{name}: {fname}={v} is a reserved {f['enum']} value")
    return v - 1 if f.get("kind") == "minus1" else v


def _check_constraints(name, vals):
    for c in INSTRS[name]["constraints"]:
        if all(vals.get(k, 0) == v for k, v in c["if"].items()):
            for k, v in c["then"].items():
                if vals.get(k, 0) != v:
                    raise IsaError(f"{name}: {k} must be {v} when "
                                   + ", ".join(f"{a}={b}" for a, b in c["if"].items()))


def encode(name, **fields):
    """Encode one instruction; missing fields are 0 (minus1 fields default to 1)."""
    name = name.upper()
    if name not in INSTRS:
        raise IsaError(f"unknown instruction {name}")
    t = INSTRS[name]
    unknown = set(fields) - set(t["fields"]) - {"ss"}
    if unknown:
        raise IsaError(f"{name}: unknown fields {sorted(unknown)}")
    lo, hi = field_range(name, "ss")
    ss = fields.get("ss", 0)
    if not lo <= ss <= hi:
        raise IsaError(f"{name}: side-set {ss} outside {lo} to {hi}")
    word = t["value"] | ss << SS[1]
    raws = {}
    for fname, f in t["fields"].items():
        default = 1 if f.get("kind") == "minus1" else 0
        raws[fname] = _raw(name, fname, fields.get(fname, default))
        word |= raws[fname] << f["lo"]
    _check_constraints(name, raws)
    return word


def decode(word):
    """Instr for a legal word, None for an illegal one (reserved bit, enum hole, constraint)."""
    for name, t in INSTRS.items():
        if word & t["mask"] != t["value"]:
            continue
        vals = {"ss": _get(word, *SS)}
        raws = {}
        for fname, f in t["fields"].items():
            v = raws[fname] = _get(word, f["hi"], f["lo"])
            if "enum" in f and v not in ENUMS[f["enum"]].values():
                return None
            vals[fname] = v + 1 if f.get("kind") == "minus1" else v
        try:
            _check_constraints(name, raws)
        except IsaError:
            return None
        return Instr(name, vals)
    return None


def is_legal(word):
    return decode(word) is not None


def cost(ins):
    """Static cost of a decoded instruction."""
    cyc = INSTRS[ins.name]["cycles"]
    base = cyc["base"]
    if "delay" in cyc:
        base += ins.fields[cyc["delay"]]
    if "cfgword" in cyc and regs.is_cfg(ins.fields["addr"]):
        base += cyc["cfgword"]
    wait = cyc.get("wait", False)
    if isinstance(wait, str):
        wait = bool(ins.fields[wait])
    return Cost(base, wait, cyc.get("dslot"))


def first_slot(c, lane):
    """First cycle s >= c that lane owns for a fetch (s mod FRAME_SLOTS == lane)."""
    return c + (lane - c) % FRAME_SLOTS


def data_slot(c, lane):
    """First cycle s >= c that is lane's data slot (s mod DATA_FRAME == 4 x lane + 3)."""
    own = FRAME_SLOTS * lane + FRAME_SLOTS - 1
    return c + (own - c) % DATA_FRAME


def dslot_cycles(kind, c, lane, fetch_latency=FETCH_LATENCY):
    """Duration of an STW ("store") or LDW ("load") dispatched in cycle c."""
    s = data_slot(c, lane)
    if kind == "store":
        return s + 1 - c
    return s + 2 - c + (fetch_latency - 1)
