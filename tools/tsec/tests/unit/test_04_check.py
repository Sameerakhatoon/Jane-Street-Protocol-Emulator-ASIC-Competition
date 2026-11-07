# SPDX-License-Identifier: Apache-2.0
import pytest

from tools.tsec.diag import Errors

from helpers import UART, codes, front

ROLE = """protocol p {{
  wire tx : out push_pull idle 1;
  wire rx : in;
  wire sda : inout open_drain pull_up;
  role r : controller uses tx, rx, sda {{
    var b : bits[8];
    state idle {{ on host.pull(b) -> f; }}
    state f {{ seq {{ {body} }} -> idle; }}
  }}
}}"""


def test_uart_is_clean():
    front(UART)


@pytest.mark.parametrize("body, code", [
    ("drive rx = 0 for 1 us;", "E206"),
    ("sample tx expect 0 for 1 us;", "E207"),
    ("drive sda = 1 for 1 us;", "E208"),
    ("shift c lsb 8 -> tx each 1 us;", "E209"),
    ("shift b lsb 9 -> tx each 1 us;", "E210"),
    ("drive tx = 0 for 1 MHz;", "E211"),
    ("shift b lsb 8 -> tx each 2;", "E211"),
    ("drive tx = 0 for 1 us ~ 1 MHz;", "E212"),
    ("sample rx expect 0 within [3 us, 1 us] after fall(rx);", "E213"),
    ("sample rx expect 0 within [1 us, 3 Hz] after fall(rx);", "E213"),
    ("shift b lsb 0 -> tx each 1 us;", "E216"),
    ("release tx;", "E218"),
    ("drive tx = 2;", "E219"),
    ("drive nope = 0;", "E204"),
    ("wait 3;", "E211"),
    ("repeat 0 { wait 1 us; }", "E216"),
])
def test_statement_checks(body, code):
    with pytest.raises(Errors) as e:
        front(ROLE.format(body=body))
    assert codes(e) == [code]


def test_good_statements():
    front(ROLE.format(body="drive tx = 0 for 1 us ~ 5 %; release sda within [0 ns, 1 us] after "
                           "fall(rx); shift b msb 3 -> tx each 1 us ~ 10 ns; sample rx; wait 2 us; "
                           "repeat 3 { push host b; }"))


def test_structure_checks():
    src = """protocol p {
  wire tx : out;
  wire rx : in;
  role r : controller uses tx, zz {
    var b : bits[8];
    state idle { on host.pull(b) -> gone; on fall(tx) -> idle; on host.bogus -> idle; }
    state lost { on start -> idle; }
  }
}"""
    with pytest.raises(Errors) as e:
        front(src)
    assert codes(e) == ["E201", "E202", "E203", "E101", "E214", "E101"]


def test_errors_sorted_by_position():
    with pytest.raises(Errors) as e:
        front(ROLE.format(body="drive rx = 2; sample tx;"))
    spans = [(x.span.line, x.span.col) for x in e.value.errs]
    assert spans == sorted(spans) and len(spans) == 3
