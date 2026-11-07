# SPDX-License-Identifier: Apache-2.0
"""Assist events and the lane resources of a frame role.

An event NAME.WHAT on an assist is a lane signal that a WAIT can watch:

    crc.ok, crc.bad        EV CRCOK (the CRC register equals its residue), level
    timer.tick             EV TIMER (TIMER_CTL.ev0_sel), sticky until a WAIT on it ends
    dpll.tick              EV BITSTART, one cycle per bit
    stuffer.err            EV ERR (a stuff violation), sticky until a frame reset
    manchester.eop, .err   EV ERR (a cell without its middle transition): after a
                           frame the line stops changing, so the first idle cell is one
    nrzi.err               EV ERR
    rule.hit               EV RULEHIT, sticky until a frame reset

Anything else has no signal in the lane (an NRZI start or end of packet, for
example: the codec samples one pin and cannot see SE0), which is a resource
rejection, not a guess.

The timing of each signal against the bit it belongs to comes from the lane
RTL (TX and RX engines, codec, stuffer, CRC) and is collected in FACTS, so
the deadlines the lowering attaches are written once, with their reason.
"""
from dataclasses import dataclass

from . import ast as A
from .diag import TseError


@dataclass(frozen=True)
class Sig:
    src: str        # EV or FLAG
    idx: str        # name in the WAIT index table
    pol: int = 1


EVENTS = {
    "crc": {"ok": Sig("EV", "CRCOK"), "bad": Sig("EV", "CRCOK", 0)},
    "timer": {"tick": Sig("EV", "TIMER")},
    "dpll": {"tick": Sig("EV", "BITSTART")},
    "stuffer": {"err": Sig("EV", "ERR")},
    "manchester": {"eop": Sig("EV", "ERR"), "err": Sig("EV", "ERR")},
    "nrzi": {"err": Sig("EV", "ERR")},
    "rule": {"hit": Sig("EV", "RULEHIT")},
}
CODECS = ("nrzi", "manchester")

# Cycle facts of the lane, all from the RTL; P is the shortest bit period in cycles.
FACTS = {
    # TXNEED is seen 1 cycle after the bit start that takes the word's last bit; the next
    # TXDATA write must land before the next bit start, or the frame ends there.
    "txneed": (-2, "the TXDATA write must land before the next bit start"),
    # RXFULL is seen 1 cycle after the last bit enters RXDATA; the next bit shifts it.
    "rxfull": (-1, "RXDATA must be read before the next bit shifts it"),
    # RULEHIT on a received bit: a frame reset in the next bit's cycle would drop that bit.
    "rulehit": (-2, "the frame reset must land before the next received bit"),
    # ERR after a Manchester frame: the CRC holds the frame plus one idle bit from 1 cycle
    # after ERR is seen until the second idle bit enters it, P cycles later.
    "eop": (0, "the CRC holds the frame and one idle bit until the next idle bit"),
}
TXIDLE_TO_PIN = 1           # TXIDLE is seen 1 cycle after the line went to its idle level
CFG_VISIBLE = 2             # a CSRW to a configuration word is used by the assists 2 cycles on


def deadline(name, period_min):
    """Latest dispatch, in cycles after the signal is seen, of the instruction serving it."""
    return period_min + FACTS[name][0]


def named(ev):
    return isinstance(ev, A.Event) and ev.kind == "named" and ev.attr is not None


def lookup(sym, ev):
    """(assist, Sig) of an assist event, or a TseError saying why the lane has no such signal."""
    a = sym.assists.get(ev.name.text)
    if a is None:
        return None, None
    what = ev.attr.text
    sig = EVENTS.get(a.kind, {}).get(what)
    if sig is None:
        have = ", ".join(sorted(EVENTS.get(a.kind, {}))) or "none"
        why = {"nrzi": "the NRZI codec samples one pin: it cannot see SE0, so it has no start "
                       "or end of packet"}.get(a.kind, "")
        raise TseError("R304", f"assist '{a.name.text}' ({a.kind}) has no event '{what}' in the "
                       f"lane (its events: {have})" + (f"; {why}" if why else ""), ev.span)
    return a, sig


def walk(x, fn):
    if isinstance(x, list):
        for y in x:
            walk(y, fn)
    elif hasattr(x, "__dataclass_fields__"):
        fn(x)
        for k in x.__dataclass_fields__:
            if k != "span":
                walk(getattr(x, k), fn)


def is_frame_role(sym, role):
    """A role that sends or receives a frame or waits for an assist event."""
    hit = []

    def fn(x):
        if isinstance(x, (A.Send, A.Recv)) or (named(x) and x.name.text in sym.assists):
            hit.append(x)
    walk(role.states, fn)
    return bool(hit)


def codec_of(sym):
    return [a for a in sym.assists.values() if a.kind in CODECS]


def check_resources(sym, role):
    """R307 and R304 for what the lane cannot do at all, before anything is lowered: a codec
    drives and samples one lane pin, and a lane has one CRC, one stuffer, one codec, one DPLL."""
    from .assists import Params
    uses = {u.text for u in role.uses}
    blockers = []
    for a in codec_of(sym):
        pins = Params(sym, a).pins("pins")
        if len(pins) != 1:
            blockers.append(TseError(
                "R307", f"pin plan impossible: assist '{a.name.text}' codes the line on "
                f"{len(pins)} wires ({', '.join(pins)}); the codec drives and samples one lane pin. "
                "A second wire can only be the first one inverted on the crossbar, so a line state "
                "with both wires low (SE0, the end of a USB packet) can be neither driven nor seen",
                a.name.span))
        elif pins[0] not in uses:
            blockers.append(TseError("R307", f"assist '{a.name.text}' codes wire '{pins[0]}', which "
                                     f"role '{role.name.text}' does not use", a.name.span))
    seen = {}
    for a in sym.assists.values():
        unit = {"crc": "a CRC", "stuffer": "a stuffer", "dpll": "a DPLL", "nrzi": "a line codec",
                "manchester": "a line codec"}.get(a.kind)
        if unit is None:
            continue
        if unit in seen:
            blockers.append(TseError("R304", f"assist resources exhausted: role '{role.name.text}' "
                                     f"needs {unit} for '{seen[unit]}' and for '{a.name.text}', a lane "
                                     "has one", a.name.span))
        seen.setdefault(unit, a.name.text)

    def fn(x):
        if named(x) and x.name.text in sym.assists:
            try:
                lookup(sym, x)
            except TseError as e:
                if not any(b.msg == e.msg for b in blockers):
                    blockers.append(e)
    walk(role.states, fn)
    if blockers:
        first = blockers[0]
        first.notes += [f"also: {b.msg} [{b.code}]" for b in blockers[1:]]
        raise first
