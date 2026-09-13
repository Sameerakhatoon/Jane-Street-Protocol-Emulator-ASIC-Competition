"""Diagnostics: one error type for every stage and the gcc-style printer.

Every stage raises or collects TseError. The driver prints them as

    file:line:col: error: message [CODE]
      18 |         drive rx = 0 for 1 bit;
         |               ^~

Codes: E0xx lexer and parser, E1xx names and dimensions, E2xx semantic
checks, R3xx rejections by rule R1. Exit codes:
0 accepted, 1 spec errors, 2 rejected, 3 internal error.
"""
from dataclasses import dataclass

EXIT_OK, EXIT_ERRORS, EXIT_REJECTED, EXIT_INTERNAL = 0, 1, 2, 3
MAX_ERRORS = 20


@dataclass(frozen=True)
class Span:
    line: int = 0
    col: int = 0
    width: int = 1

    def __str__(self):
        return f"{self.line}:{self.col}"


NOWHERE = Span()


class TseError(Exception):
    def __init__(self, code, msg, span=NOWHERE, notes=()):
        super().__init__(msg)
        self.code, self.msg, self.span, self.notes = code, msg, span, list(notes)

    @property
    def is_rejection(self):
        return self.code.startswith("R")


class Errors(Exception):
    """Several diagnostics from one stage, raised together."""

    def __init__(self, errs):
        super().__init__(f"{len(errs)} error(s)")
        self.errs = sorted(errs, key=lambda e: (e.span.line, e.span.col))


def render(e, src, path):
    lines = src.splitlines()
    where = f"{path}:{e.span.line}:{e.span.col}" if e.span.line else path
    out = [f"{where}: error: {e.msg} [{e.code}]"]
    if 0 < e.span.line <= len(lines):
        text = lines[e.span.line - 1]
        num = str(e.span.line)
        pad = " " * len(num)
        mark = "^" + "~" * (max(1, e.span.width) - 1)
        out.append(f" {num} | {text}")
        out.append(f" {pad} | {' ' * (e.span.col - 1)}{mark}")
    out += [f"  note: {n}" for n in e.notes]
    return "\n".join(out)
