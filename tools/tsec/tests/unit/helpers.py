"""Small builders shared by the unit tests."""
from fractions import Fraction as Fr

from tools.tsec.check import check
from tools.tsec.parser import parse
from tools.tsec.resolve import resolve

FCLK = Fr(60_000_000)

with open("specs/uart.tse") as fh:
    UART = fh.read()


def front(src, **over):
    sym = resolve(parse(src), {k: str(v) for k, v in over.items()})
    check(sym)
    return sym


def role(sym, name):
    return next(r for r in sym.roles if r.name.text == name)


def codes(excinfo):
    return [e.code for e in excinfo.value.errs]
