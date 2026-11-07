# SPDX-License-Identifier: Apache-2.0
"""08: the model's hand transcription against the decoder generated from isa/tse_isa.yaml.

Two independent readings of the same table must agree on every word: which are
legal, which instruction each one is, and which lane registers cost 3 cycles.
"""
import pytest

tse = pytest.importorskip("tools.tseasm.isa")
regs = pytest.importorskip("tools.tseasm.regs")

from model.isa import CLASS_L, Illegal, decode  # noqa: E402


def test_same_legal_words_and_names():
    for w in range(1 << 16):
        theirs = tse.decode(w)
        try:
            ours = decode(w)[0]
        except Illegal:
            ours = None
        assert ours == (theirs.name if theirs else None), hex(w)


def test_same_configuration_words():
    assert CLASS_L == frozenset(a for a in range(256) if regs.is_cfg(a))
