# SPDX-License-Identifier: Apache-2.0
"""A `recv` with a byte count whose own answer field comes after the CRC and a
delimiter (a CAN ACK): layout check, the RX bookkeeping between frames, the
quiet tail in the rearm deadline and the pin keys the loader reads."""
import json
import subprocess
import sys

import pytest

from tools.tsec.diag import TseError
from tools.tsec.framelower import FrameLowering

from helpers import FCLK, front, role

with open("specs/can_rx_ack.tse") as fh:
    CAN = fh.read()


def lowered(src=CAN, **over):
    sym = front(src, **over)
    return FrameLowering(sym, role(sym, "node"), FCLK).lower()


def at(low, label):
    return next(k for k, i in enumerate(low.prog) if i.label == label)


def test_ack_after_crc_delimiter_lowers():
    low = lowered()
    assert low.rules[0].cond == "CRC_GOOD" and low.rules[0].rlen == 1
    assert low.ack_field.name == "ack"


def test_answer_field_before_the_crc_is_rejected():
    src = CAN.replace("crc_del : bit = 1 nostuff;  ack : bit = 1 nostuff from node;",
                      "crc_del : bit = 1 nostuff;  ack : bit = 1 nostuff;") \
             .replace("sof : bit = 0;", "sof : bit = 0 from node;")
    with pytest.raises(TseError) as e:
        lowered(src)
    assert e.value.code == "R300" and "layout of frame 'can_std'" in str(e.value)


def test_arm_drops_a_stale_rx_word():
    low = lowered()
    k = next(k for k, i in enumerate(low.prog) if getattr(i, "mark", None) == "arm")
    assert low.prog[k + 1].op == "CSRR" and low.prog[k + 1].a["addr"] == 0x22


def test_tail_word_is_read_before_the_crc_test():
    low = lowered()
    k = next(k for k, i in enumerate(low.prog) if i.op == "WAIT" and i.a.get("to"))
    assert low.prog[k - 1].op == "CSRR" and low.prog[k - 2].note.startswith("CRC and the bits")


@pytest.mark.parametrize("path", ["good", "bad"])
def test_header_word_length_restored_after_the_answer_field(path):
    low = lowered()
    k = next(k for k, i in enumerate(low.prog) if (i.label or "").startswith(path))
    body = []
    for i in low.prog[k + 1:]:
        if i.op == "JMP" and i.a["cond"] == "ALWAYS":
            break
        body.append(i)
    assert [i.a["idx"] for i in body if i.op == "WAIT"] == [5, 5]
    assert body[-1].op == "CSRW" and body[-1].note == "RX words of 19 bits again"


@pytest.mark.parametrize("rate", ["125e3", "250e3", "500e3"])
def test_rearm_counts_the_recessive_tail(rate):
    low = lowered(bitrate=rate)
    assert "ack_del..eof (8 bits)" in low.rearm.why
    assert low.rearm.hi == int(7 * low.p_min) - 1


def test_frame_pins_carry_loader_keys(tmp_path):
    subprocess.run([sys.executable, "-m", "tools.tsec", "build", "specs/can_rx_ack.tse", "--emit", "json",
                    "-o", str(tmp_path)], check=True, capture_output=True)
    pins = json.load(open(tmp_path / "can_rx_ack.json"))["lanes"][0]["pins"]
    assert pins["txd"]["vpin_out"] == pins["txd"]["vpin"] == 0
    assert pins["rxd"]["vpin_in"] == pins["rxd"]["vpin"] == 1 and "vpin_out" not in pins["rxd"]
