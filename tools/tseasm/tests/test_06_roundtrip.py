"""Hypothesis round trips over random legal instructions and random programs."""
from hypothesis import given, settings
from hypothesis import strategies as st

from tools.tseasm import assemble, disasm, disasm_word, isa

CFG = settings(database=None, deadline=None)


@st.composite
def instruction(draw, length=256):
    """A random legal word built field by field from the ISA table."""
    name = draw(st.sampled_from(list(isa.INSTRS)))
    fields = {"ss": draw(st.integers(*isa.field_range(name, "ss")))}
    for fname, f in isa.INSTRS[name]["fields"].items():
        if "enum" in f:
            fields[fname] = draw(st.sampled_from(sorted(set(isa.ENUMS[f["enum"]].values()))))
        elif f.get("kind") == "addr":
            fields[fname] = draw(st.integers(0, length - 1))
        else:
            fields[fname] = draw(st.integers(*isa.field_range(name, fname)))
    if fields.get("ind"):
        fields["addr"] = 0
    return isa.encode(name, **fields)


# 1: word to text to word, and text to word to text, for random legal instructions
@settings(CFG, max_examples=2000)
@given(instruction())
def test_01_instruction_round_trip(w):
    text = disasm_word(w)
    assert assemble(text).words == [w]
    assert disasm_word(assemble(text).words[0]) == text


# 2: disassemble then reassemble a random program (with labels) gives the same words and text
@settings(CFG, max_examples=300)
@given(st.integers(1, 40).flatmap(lambda n: st.lists(instruction(n), min_size=n, max_size=n)))
def test_02_program_round_trip(words):
    text = disasm(words)
    again = assemble(text).words
    assert again == words
    assert disasm(again) == text
