"""Obligation files (JSON).

    {
      "clock_hz": 60e6,
      "codes": {"start": 1, "scl_fall": 2, "viol_hd_sta": 9},
      "obligations": [
        {"name": "tHD;STA", "from": "start", "to": "scl_fall", "min": "4.0us", "lane": 1},
        {"name": "flagged", "forbid": "viol_hd_sta"},
        {"name": "tSU;STA", "from": "scl_rise", "to": "start", "min": "4.7us",
         "same_lane": false, "cancel": ["stop"]}
      ]
    }

Codes are names from "codes" or plain numbers; a program code must be at
most the highest EMIT code. Times are numbers of ticks, or strings with a
unit: "240t", "4.0us", "100ns", "1ms", "2s"; times convert to ticks at
clock_hz and stay fractional when they fall between ticks.
"""
import json
import re

from . import records
from .report import Obligation

_UNITS = {"t": None, "s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}
_TIME_RE = re.compile(r"^\s*([0-9]*\.?[0-9]+(?:[eE][-+]?[0-9]+)?)\s*(t|s|ms|us|ns)\s*$")


class ObError(ValueError):
    pass


def ticks(v, clock_hz):
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return v
    m = _TIME_RE.match(str(v))
    if not m:
        raise ObError("bad time %r" % (v,))
    num, unit = float(m.group(1)), m.group(2)
    if _UNITS[unit] is None:
        return num
    t = num * _UNITS[unit] * clock_hz
    return int(round(t)) if abs(t - round(t)) < 1e-9 else t


def _code(v, codes):
    c = codes.get(v) if isinstance(v, str) else v
    if not isinstance(c, int) or isinstance(c, bool) or not 0 <= c <= records.CODE_MAX:
        raise ObError("bad event code %r" % (v,))
    return c


def load(text, clock_hz=None):
    """(clock_hz, [Obligation]) from the JSON text; clock_hz overrides the file."""
    try:
        d = json.loads(text)
    except ValueError as e:
        raise ObError("not JSON: %s" % e)
    clock = float(clock_hz or d.get("clock_hz", 0))
    if clock <= 0:
        raise ObError("no clock_hz")
    codes = d.get("codes", {})
    obs = []
    for o in d.get("obligations", []):
        name = o.get("name") or "?"
        try:
            lim = {k: ticks(o[k], clock) for k in ("min", "max") if k in o}
            if "forbid" in o:
                obs.append(Obligation(name, forbid=_code(o["forbid"], codes), lane=o.get("lane"), **lim))
            else:
                same = o.get("same_lane", True)
                cancel = o.get("cancel", [])
                if not isinstance(same, bool) or not isinstance(cancel, list):
                    raise ObError("same_lane is true or false, cancel a list of codes")
                obs.append(Obligation(name, _code(o.get("from"), codes), _code(o.get("to"), codes),
                                      lane=o.get("lane"), same_lane=same,
                                      cancel=[_code(c, codes) for c in cancel], **lim))
        except ValueError as e:
            raise ObError("%s: %s" % (name, e))
    if not obs:
        raise ObError("no obligations")
    return clock, obs
