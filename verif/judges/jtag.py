"""JTAG judge: TAP controller states, IR and DR scans, IDCODE reads.

Written from IEEE 1149.1 (test access port and boundary-scan
architecture). The TAP controller is a 16-state machine that moves on
every rising edge of TCK according to TMS. TMS and TDI are sampled on the
rising edge; the device changes TDO on the falling edge, so the host also
reads TDO at the rising edge. While the controller is in Shift-IR or
Shift-DR, every rising edge shifts one bit, including the edge that leaves
for Exit1. A scan runs from Capture to Update and may pause (Pause, Exit2,
back to Shift); the judge reports it when it reaches Update.

The judge does not know the state at the start of a trace. It locks on
five rising edges with TMS high (which reach Test-Logic-Reset from any
state) or on TRST low. In Test-Logic-Reset the instruction register holds
IDCODE, or BYPASS in a device without an identification register, so a DR
scan before the first IR scan reads the chain's identification codes: a
device with IDCODE shifts out 32 bits whose bit 0 is 1, a device in BYPASS
shifts out one 0 bit.

Frame keys: "RESET", "IR 4 TDI=E TDO=1", "DR 32 TDI=00000000 TDO=4BA00477",
"IDCODE 4BA00477" (several devices: "IDCODE 4BA00477,BYPASS,06431041",
nearest TDO first). Values are hexadecimal with the first shifted bit as
bit 0; without a TDO role the TDO part is left out.
"""
from .common import Result
from .vcd import X, Z

ROLES = ("tck", "tms", "tdi", "tdo", "trst")
REQUIRED = ("tck", "tms")
PULLUP = False
OPTS = ("tdo_z", "assume_reset")

TLR, RTI = "Test-Logic-Reset", "Run-Test/Idle"
# state: (next with TMS=0, next with TMS=1)
NEXT = {
    TLR: (RTI, TLR),
    RTI: (RTI, "Select-DR-Scan"),
    "Select-DR-Scan": ("Capture-DR", "Select-IR-Scan"),
    "Capture-DR": ("Shift-DR", "Exit1-DR"),
    "Shift-DR": ("Shift-DR", "Exit1-DR"),
    "Exit1-DR": ("Pause-DR", "Update-DR"),
    "Pause-DR": ("Pause-DR", "Exit2-DR"),
    "Exit2-DR": ("Shift-DR", "Update-DR"),
    "Update-DR": (RTI, "Select-DR-Scan"),
    "Select-IR-Scan": ("Capture-IR", TLR),
    "Capture-IR": ("Shift-IR", "Exit1-IR"),
    "Shift-IR": ("Shift-IR", "Exit1-IR"),
    "Exit1-IR": ("Pause-IR", "Update-IR"),
    "Pause-IR": ("Pause-IR", "Exit2-IR"),
    "Exit2-IR": ("Shift-IR", "Update-IR"),
    "Update-IR": (RTI, "Select-DR-Scan"),
}
SHIFT = ("Shift-DR", "Shift-IR")


def add_args(p):
    p.add_argument("--tdo-z", action="store_true",
                   help="TDO must be high impedance outside Shift-IR and Shift-DR")
    p.add_argument("--assume-reset", action="store_true",
                   help="start in Test-Logic-Reset (power-up) instead of waiting for a reset")


def hexbits(bits):
    v = 0
    for k, b in enumerate(bits):
        v |= b << k
    return "%0*X" % (max(1, (len(bits) + 3) // 4), v)


def idcodes(bits):
    """Split the TDO bits of a DR scan after reset into identification codes."""
    out = []
    k = 0
    while k < len(bits):
        if bits[k] == 0:
            out.append("BYPASS")
            k += 1
            continue
        if k + 32 > len(bits):
            break
        v = 0
        for j in range(32):
            v |= bits[k + j] << j
        if v == 0xFFFFFFFF:     # TDI ones coming back: end of the chain
            break
        out.append("%08X" % v)
        k += 32
    return out


def decode(trace, tdo_z=False, assume_reset=False):
    res = Result(trace)
    tck, tms = trace["tck"], trace["tms"]
    tdi = trace["tdi"] if "tdi" in trace else None
    tdo = trace["tdo"] if "tdo" in trace else None
    trst = trace["trst"] if "trst" in trace else None
    rises = tck.edges(-1, trace.end, 1)
    state = TLR if assume_reset else None
    ones = 0
    ir_loaded = False
    scan = None         # [kind, t0, tdi bits, tdo bits]
    t_prev = -1
    lost_at = rises[0] if rises else 0

    for r in rises:
        if trst is not None and (trst.before(r) == 0 or trst.edges(t_prev, r, 0)):
            if state != TLR:
                res.frame(r, r, "RESET", "TRST")
            state, ir_loaded, scan, ones = TLR, False, None, 0
        t_prev = r
        m = tms.before(r)
        if m not in (0, 1):
            res.violate(r, "unknown level", "X" if m == X else "Z", "0 or 1", text="TMS at a TCK rise")
            state, scan, lost_at = None, None, r
            continue
        ones = ones + 1 if m else 0
        if state is None:
            if ones >= 5:
                state, ir_loaded = TLR, False
                res.frame(r, r, "RESET")
            continue
        if state in SHIFT:
            d = tdi.before(r) if tdi is not None else 0
            if d not in (0, 1):
                res.violate(r, "unknown level", "X" if d == X else "Z", "0 or 1", text="TDI in " + state)
                d = 0
            o = tdo.before(r) if tdo is not None else 0
            if o not in (0, 1):
                res.violate(r, "TDO not driven", "X" if o == X else "Z", "0 or 1", text="in " + state)
                o = 0
            scan[2].append(d)
            scan[3].append(o)
        elif tdo is not None and tdo_z and tdo.before(r) in (0, 1):
            res.violate(r, "TDO active outside Shift", tdo.before(r), "Z", text="in " + state)
        nxt = NEXT[state][m]
        if nxt in ("Capture-DR", "Capture-IR"):
            scan = [nxt[-2:], r, [], []]
        elif nxt in ("Update-DR", "Update-IR"):
            kind, t0, din, dout = scan
            ids = idcodes(dout) if kind == "DR" and not ir_loaded and tdo is not None else []
            if ids:
                key = "IDCODE " + ",".join(ids)
                _check_ids(res, t0, ids)
            else:
                key = "%s %d TDI=%s" % (kind, len(din), hexbits(din))
                if tdo is not None:
                    key += " TDO=" + hexbits(dout)
            if kind == "IR":
                ir_loaded = True
            res.frame(t0, r, key)
            scan = None
        elif nxt == TLR and state != TLR:
            ir_loaded = False
            res.frame(r, r, "RESET")
        state = nxt

    if state is None and rises:
        res.violate(lost_at, "no TAP reset", "state unknown", "5 TCK rises with TMS high or TRST low")
    if scan is not None:
        res.violate(scan[1], "incomplete scan", "%d bits, trace ended" % len(scan[2]), "Update-" + scan[0])
    return res.sort()


def _check_ids(res, t, ids):
    for v in ids:
        if v != "BYPASS" and (int(v, 16) >> 1) & 0x7FF == 0x7F:
            res.violate(t, "IDCODE manufacturer", "0x7F", "not 0x7F (reserved)", text=v)
