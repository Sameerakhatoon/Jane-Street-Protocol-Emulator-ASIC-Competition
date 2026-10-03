"""Streaming VCD reader for the judges.

The file is read once, token by token, and only the signals asked for are
kept: per signal an array of change times (in file time units) and a
bytearray of levels, so a long trace with many unrelated variables costs
memory only for the pins being judged. Levels are 0, 1, X and Z.

A signal is named by its full dotted path (top.uo_out), by any dotted
suffix or leaf name that is unique, and optionally with a bit select
(uo_out[3]) into a vector variable. Bit-blasted variables declared as
"name [3]" are found by the same spelling.
"""
import bisect
import re
from array import array

X = 2
Z = 3

_LEVEL = {"0": 0, "1": 1, "x": X, "X": X, "z": Z, "Z": Z, "u": X, "U": X,
          "w": X, "W": X, "-": X, "l": 0, "L": 0, "h": 1, "H": 1}
_UNITS = {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9, "ps": 1e-12, "fs": 1e-15}
_SELECT = re.compile(r"^(.*?)\s*\[(\d+)\]$")
_RANGE = re.compile(r"^\[(\d+)(?::(\d+))?\]$")


class VcdError(Exception):
    pass


class Signal:
    """Changes of one judged signal. Times are file units, levels 0/1/X/Z."""

    def __init__(self, name, pullup=False):
        self.name = name
        self.pullup = pullup
        self.times = array("q")
        self.levels = bytearray()

    def add(self, t, v):
        if v == Z and self.pullup:
            v = 1
        if self.times and self.times[-1] == t:
            # several changes at one instant: the last one is the level
            self.times.pop()
            self.levels.pop()
        if self.levels and self.levels[-1] == v:
            return
        self.times.append(t)
        self.levels.append(v)

    def at(self, t):
        """Level after every change at time t."""
        i = bisect.bisect_right(self.times, t) - 1
        return self.levels[i] if i >= 0 else X

    def before(self, t):
        """Level just before time t (ignores a change exactly at t)."""
        i = bisect.bisect_left(self.times, t) - 1
        return self.levels[i] if i >= 0 else X

    def first_after(self, t):
        """Index of the first change strictly after t (len if none)."""
        return bisect.bisect_right(self.times, t)

    def first_at_or_after(self, t):
        return bisect.bisect_left(self.times, t)

    def last_change_before(self, t):
        """Time of the last change strictly before t, or None."""
        i = bisect.bisect_left(self.times, t) - 1
        return self.times[i] if i >= 0 else None

    def edges(self, lo, hi, to=None):
        """Change times in (lo, hi], optionally only those that go to level to."""
        out = []
        i = bisect.bisect_right(self.times, lo)
        n = len(self.times)
        while i < n and self.times[i] <= hi:
            if to is None or self.levels[i] == to:
                out.append(self.times[i])
            i += 1
        return out

    def __len__(self):
        return len(self.times)


class Trace:
    """The judged signals of one VCD file, keyed by role."""

    def __init__(self, ts, signals, end):
        self.ts = ts            # seconds per file time unit
        self.signals = signals
        self.end = end          # last time stamp in the file

    def __getitem__(self, role):
        return self.signals[role]

    def __contains__(self, role):
        return role in self.signals

    def units(self, seconds):
        return seconds / self.ts

    def seconds(self, units):
        return units * self.ts


class _Var:
    __slots__ = ("code", "width", "right", "path")

    def __init__(self, code, width, right, path):
        self.code = code
        self.width = width
        self.right = right
        self.path = path


def _tokens(f):
    for line in f:
        for tok in line.split():
            yield tok


def _until_end(tok):
    out = []
    for t in tok:
        if t == "$end":
            return out
        out.append(t)
    raise VcdError("unexpected end of file inside a header command")


def _timescale(words):
    s = "".join(words)
    m = re.match(r"^(\d+)\s*([a-z]+)$", s)
    if not m or m.group(2) not in _UNITS:
        raise VcdError("bad timescale %r" % s)
    return int(m.group(1)) * _UNITS[m.group(2)]


def _resolve(vars_, spec):
    """Find the variable for spec; returns (var, bit or None)."""
    def matches(path):
        return path == spec or path.endswith("." + spec)

    found = {}
    for v in vars_:
        if matches(v.path):
            found[v.code] = (v, None)
    if not found:
        m = _SELECT.match(spec)
        if m:
            base, bit = m.group(1), int(m.group(2))
            for v in vars_:
                if (v.path == base or v.path.endswith("." + base)) and v.width > 1:
                    found[v.code] = (v, bit)
    if not found:
        raise VcdError("signal %r not found in the VCD" % spec)
    if len(found) > 1:
        names = sorted(v.path for v, _ in found.values())
        raise VcdError("signal %r is ambiguous: %s" % (spec, ", ".join(names)))
    return next(iter(found.values()))


def _vector_bit(value, width, off):
    if len(value) < width:
        pad = value[0] if value[0] in "xXzZ" else "0"
        value = pad * (width - len(value)) + value
    if off >= len(value):
        return X
    return _LEVEL.get(value[-1 - off], X)


def read_vcd(path, mapping, pullup=False):
    """Read path keeping the signals of mapping {role: signal spec}.

    pullup=True maps Z to 1 (open-drain buses with a pull-up resistor).
    """
    with open(path, "r", errors="replace") as f:
        tok = _tokens(f)
        ts = 1e-9
        scopes = []
        vars_ = []
        for t in tok:
            if t == "$enddefinitions":
                _until_end(tok)
                break
            if t == "$timescale":
                ts = _timescale(_until_end(tok))
            elif t == "$scope":
                w = _until_end(tok)
                scopes.append(w[1] if len(w) > 1 else w[0])
            elif t == "$upscope":
                _until_end(tok)
                if scopes:
                    scopes.pop()
            elif t == "$var":
                w = _until_end(tok)
                if len(w) < 4:
                    raise VcdError("bad $var: %s" % " ".join(w))
                width, code, name = int(w[1]), w[2], w[3]
                right = 0
                rng = w[4] if len(w) > 4 else None
                if rng is None and "[" in name and name.endswith("]"):
                    i = name.index("[")
                    name, rng = name[:i], name[i:]
                if rng is not None:
                    m = _RANGE.match(rng)
                    if m and width == 1:
                        name = "%s[%s]" % (name, m.group(1))
                    elif m:
                        right = int(m.group(2) if m.group(2) is not None else m.group(1))
                vars_.append(_Var(code, width, right, ".".join(scopes + [name])))
            elif t.startswith("$"):
                _until_end(tok)
        else:
            raise VcdError("no $enddefinitions in %s" % path)

        signals = {}
        scalar = {}     # code -> [Signal]
        vector = {}     # code -> [(Signal, width, offset)]
        for role, spec in mapping.items():
            v, bit = _resolve(vars_, spec)
            s = Signal(spec, pullup)
            signals[role] = s
            if bit is None and v.width == 1:
                scalar.setdefault(v.code, []).append(s)
            else:
                b = v.right if bit is None else bit
                if bit is None and v.width != 1:
                    raise VcdError("signal %r is %d bits wide; select one bit" % (spec, v.width))
                if abs(b - v.right) >= v.width:
                    raise VcdError("bit %d is outside %s" % (b, v.path))
                vector.setdefault(v.code, []).append((s, v.width, abs(b - v.right)))

        now = 0
        for t in tok:
            c = t[0]
            if c == "#":
                now = int(t[1:])
            elif c in "bB":
                code = next(tok)
                for s, width, off in vector.get(code, ()):
                    s.add(now, _vector_bit(t[1:], width, off))
            elif c in "rR":
                next(tok)
            elif c == "$":
                if t == "$comment":
                    _until_end(tok)
            else:
                ss = scalar.get(t[1:])
                if ss:
                    v = _LEVEL.get(c, X)
                    for s in ss:
                        s.add(now, v)
                else:
                    for s, width, off in vector.get(t[1:], ()):
                        s.add(now, _vector_bit(c, width, off))
    return Trace(ts, signals, now)
