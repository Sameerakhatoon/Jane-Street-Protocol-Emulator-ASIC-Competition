"""ISA table and generator: the table loads, gives the reference encodings, and broken tables fail loudly."""
import copy

import pytest
import yaml

from tools import gen_isa
from tools.tseasm import isa

SRC = "isa/tse_isa.yaml"


def load_src():
    with open(SRC) as fh:
        return yaml.safe_load(fh)


def gen_from(tmp_path, src):
    p = tmp_path / "t.yaml"
    p.write_text(yaml.safe_dump(src))
    return gen_isa.load(str(p))


# 1: all 16 instructions of ISA version 1, in table order
def test_01_sixteen_instructions():
    assert list(isa.INSTRS) == ["JMP", "WAIT", "SET", "MOV", "OUT", "IN", "PUSH", "PULL", "CSRW",
                                "STW", "CSRR", "LDW", "DLY", "TMR", "SIG", "EMIT"]
    assert (isa.VERSION, isa.WORD_BITS, isa.FRAME_SLOTS, isa.FETCH_ENTRIES,
            isa.FETCH_LATENCY, isa.DATA_FRAME) == (1, 16, 4, 2, 1, 16)


# 2: reference encodings
@pytest.mark.parametrize("name,fields,word", [
    ("JMP", dict(cond="XDEC", target=6, ss=1), 0x0A06),
    ("OUT", dict(dst="PINS", n=1, delay=6), 0x8006),
    ("EMIT", dict(code=5, delay=2), 0xE62A),
    ("WAIT", dict(pol=1, src="PIN", idx=5, alt=1), 0x24A8),
    ("LDW", dict(ind=1), 0xC780),
])
def test_02_sample_encodings(name, fields, word):
    assert isa.encode(name, **fields) == word


# 3: the committed Python table is what the generator makes from the YAML
def test_03_table_is_current():
    assert gen_isa.load(SRC) == isa.TABLE


def broken(mutate):
    src = load_src()
    mutate(src)
    return src


# 4: each kind of table error stops the generator
@pytest.mark.parametrize("what,mutate", [
    ("overlaps another field", lambda s: s["instructions"]["SET"]["fields"]["imm"].update(bits=[8, 0])),
    ("outside the 16-bit word", lambda s: s["instructions"]["DLY"]["fields"]["d"].update(bits=[16, 0])),
    ("encodings of (SIG and EMIT|EMIT and SIG) overlap", lambda s: s["instructions"]["SIG"]["match"].update(sub=3)),
    ("does not fit", lambda s: s["instructions"]["JMP"]["match"].update(op=8)),
    ("does not fit", lambda s: s["enums"]["cond"].update(BIG=8)),
    ("unknown field", lambda s: s["instructions"]["LDW"]["constraints"][0]["then"].update(nope=0)),
    ("unknown keys", lambda s: s["instructions"]["MOV"]["fields"]["src"].update(bitz=1)),
    ("share a value", lambda s: s["enums"]["wsrc"].update(PIN2=0)),
    ("not a string", lambda s: s["enums"]["outdst"].update({None: 6})),
    ("unknown cycles term", lambda s: s["instructions"]["SIG"]["cycles"].update(stall=1)),
    ("dslot must be load or store", lambda s: s["instructions"]["LDW"]["cycles"].update(dslot="read")),
])
def test_04_broken_tables_fail(tmp_path, what, mutate):
    with pytest.raises(gen_isa.GenError, match=what):
        gen_from(tmp_path, broken(mutate))


# 5: the unbroken table round-trips through YAML dump and load unchanged
def test_05_dump_and_reload(tmp_path):
    assert gen_from(tmp_path, copy.deepcopy(load_src())) == isa.TABLE
