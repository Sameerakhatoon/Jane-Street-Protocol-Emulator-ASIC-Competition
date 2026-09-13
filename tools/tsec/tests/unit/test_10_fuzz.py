"""The front end never crashes: any input gives a tree or a diagnostic, never a Python exception."""
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tools.tsec.check import check
from tools.tsec.diag import Errors
from tools.tsec.lexer import lex
from tools.tsec.parser import parse
from tools.tsec.resolve import resolve

from helpers import UART

VOCAB = ["protocol", "param", "const", "unit", "wire", "role", "state", "on", "seq", "respond",
         "drive", "shift", "sample", "push", "host", "within", "after", "for", "each", "lsb",
         "->", "{", "}", "(", ")", "[", "]", ";", ":", ",", "=", "~", "/", "*", "+", "-", "%",
         "==", "and", "not", "fall", "rise", "x", "tx", "1", "0x1F", "2.5", "us", "MHz", "bits"]
TOKENS = [t.text for t in lex(UART)][:-1]
SETTINGS = settings(max_examples=1500, deadline=None, suppress_health_check=[HealthCheck.too_slow])


def front_end(src):
    try:
        check(resolve(parse(src)))
    except Errors as e:
        assert e.errs and all(x.code[0] in "ER" for x in e.errs)


@SETTINGS
@given(st.lists(st.sampled_from(VOCAB), max_size=60))
def test_random_token_streams(words):
    front_end("protocol p { " + " ".join(words) + " }")


@SETTINGS
@given(st.lists(st.tuples(st.sampled_from(["drop", "dup", "swap", "replace"]),
                          st.integers(0, len(TOKENS) - 1), st.sampled_from(VOCAB)),
                min_size=1, max_size=4))
def test_mutations_of_the_uart_spec(edits):
    toks = list(TOKENS)
    for op, i, word in edits:
        i %= len(toks)
        if op == "drop":
            del toks[i]
        elif op == "dup":
            toks.insert(i, toks[i])
        elif op == "swap":
            j = (i + 1) % len(toks)
            toks[i], toks[j] = toks[j], toks[i]
        else:
            toks[i] = word
        if not toks:
            toks = ["protocol"]
    front_end(" ".join(toks))


@SETTINGS
@given(st.text(max_size=80))
def test_arbitrary_text(s):
    front_end(s)
