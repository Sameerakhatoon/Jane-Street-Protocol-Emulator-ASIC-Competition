# SPDX-License-Identifier: Apache-2.0
"""Stage 1, lexer: characters to tokens with line and column.

Hand-written, one character of lookahead, like the C lexer it is modelled on.
Token kinds: "num" (value is an exact Fraction), "name", "sym", "eof".
Keywords are not a separate kind: the parser matches them by text, because
specs use reserved words such as `idle` and `frame` as state names.
Numbers stay exact rationals, so 1 / 115200 Hz is never rounded here; only
the rounding helpers in units.py round.
"""
from dataclasses import dataclass
from fractions import Fraction

from .diag import Span, TseError

TWO_CHAR_SYMS = {"->", "==", "!=", "<=", ">=", "<<", ">>", ".."}
ONE_CHAR_SYMS = set("{}()[];:,=~/*+-.%&|!<>")


@dataclass(frozen=True)
class Token:
    kind: str
    text: str
    span: Span
    value: Fraction = None

    def __str__(self):
        return "end of file" if self.kind == "eof" else f"'{self.text}'"


class Lexer:
    def __init__(self, src):
        self.src, self.pos, self.line, self.col = src, 0, 1, 1

    def peekc(self, k=0):
        i = self.pos + k
        return self.src[i] if i < len(self.src) else ""

    def nextc(self):
        c = self.src[self.pos]
        self.pos += 1
        if c == "\n":
            self.line, self.col = self.line + 1, 1
        else:
            self.col += 1
        return c

    def skip_space_and_comments(self):
        while True:
            c = self.peekc()
            if c in (" ", "\t", "\r", "\n"):
                self.nextc()
            elif c == "/" and self.peekc(1) == "/":
                while self.peekc() not in ("", "\n"):
                    self.nextc()
            else:
                return

    def tokens(self):
        out = []
        while True:
            self.skip_space_and_comments()
            line, col, start = self.line, self.col, self.pos
            c = self.peekc()
            if c == "":
                out.append(Token("eof", "", Span(line, col)))
                return out
            if is_digit(c):
                value = self.number(line, col)
                kind = "num"
            elif c.isalpha() or c == "_":
                while self.peekc().isalnum() or self.peekc() == "_":
                    self.nextc()
                kind, value = "name", None
            elif c + self.peekc(1) in TWO_CHAR_SYMS:
                self.nextc()
                self.nextc()
                kind, value = "sym", None
            elif c in ONE_CHAR_SYMS:
                self.nextc()
                kind, value = "sym", None
            else:
                raise TseError("E001", f"unexpected character {c!r}", Span(line, col))
            text = self.src[start:self.pos]
            out.append(Token(kind, text, Span(line, col, len(text)), value))

    def digits(self, ok):
        s = ""
        while ok(self.peekc()) or (self.peekc() == "_" and s):
            c = self.nextc()
            if c != "_":
                s += c
        return s

    def number(self, line, col):
        if self.peekc() == "0" and self.peekc(1) in ("x", "b"):
            self.nextc()
            base = 16 if self.nextc() == "x" else 2
            alphabet = "0123456789abcdefABCDEF" if base == 16 else "01"
            body = self.digits(lambda c: c != "" and c in alphabet)
            if not body or self.peekc().isalnum():
                while self.peekc().isalnum():
                    self.nextc()
                raise TseError("E002", "malformed number", Span(line, col, self.col - col))
            return Fraction(int(body, base))
        whole = self.digits(is_digit)
        frac = ""
        if self.peekc() == "." and is_digit(self.peekc(1)):
            self.nextc()
            frac = self.digits(is_digit)
        return Fraction(f"{whole}.{frac}") if frac else Fraction(int(whole))


def is_digit(c):
    return c != "" and c in "0123456789"


def lex(src):
    return Lexer(src).tokens()
