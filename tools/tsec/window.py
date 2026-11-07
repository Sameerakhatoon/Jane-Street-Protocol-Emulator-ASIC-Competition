# SPDX-License-Identifier: Apache-2.0
"""The data window plan: which words of the shared data window a role uses.

The data window is shared by the two lanes and the host: 128 words of 32
bits in an SRAM window of its own. LDW loads a word into OSR and STW stores
ISR, each in the lane's own data slot, so their durations are exact once the
slot phase is known.

Each role gets a contiguous region, lane after lane. Arrays (`words[N]`)
come first, each aligned to its own size, so an index taken from data is
joined to the base with one shift (base | index, no adder needed). Single
words follow on demand: the home of a variable that must outlive the shift
registers, a constant wider than an immediate, and one scratch word. Their
initial values (variable initialisers, constants) are part of the image: the
host loads them before RUN. A region that does not fit the build is R308.
"""
from dataclasses import dataclass

from .diag import TseError

DATA_WORDS = 128                # words in the data window


@dataclass
class Word:
    addr: int
    size: int
    kind: str                   # array, var, const, scratch
    name: str
    init: int = None            # loaded by the host before RUN (None: not defined)


class WindowPlan:
    def __init__(self, role, base, limit):
        self.role, self.base, self.limit = role, base, limit
        self.end = base
        self.words = {}

    def take(self, key, size, kind, name, init=None, align=1):
        w = self.words.get(key)
        if w is not None:
            return w
        addr = -(-self.end // align) * align
        if addr + size > self.limit:
            words = f"word {addr}" if size == 1 else f"words {addr} to {addr + size - 1}"
            raise TseError("R308", f"data window full: role '{self.role.name.text}' needs {words} for "
                           f"{name}, the window has {self.limit} (words {self.base} up are this "
                           "role's)", self.role.name.span)
        w = self.words[key] = Word(addr, size, kind, name, init)
        self.end = addr + size
        return w

    def array(self, name, size):
        return self.take(("array", name), size, "array", f"array {name}", align=size).addr

    def home(self, name, init):
        return self.take(("var", name), 1, "var", f"variable {name}", init).addr

    def const(self, value):
        return self.take(("const", value), 1, "const", f"constant 0x{value:X}", value).addr

    def scratch(self):
        return self.take(("scratch",), 1, "scratch", "scratch").addr

    def rows(self):
        return sorted(self.words.values(), key=lambda w: w.addr)
