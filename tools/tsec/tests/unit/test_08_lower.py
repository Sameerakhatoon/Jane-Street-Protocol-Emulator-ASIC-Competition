import pytest

from tools.tsec.diag import TseError
from tools.tsec.lower import Lowering, frame_shape

from helpers import FCLK, UART, front, role


def ops(low):
    return [i.op for i in low.prog]


def lowered(name, mode, **over):
    sym = front(UART, **over)
    low = Lowering(sym, role(sym, name), FCLK, mode)
    low.lower()
    return low


def test_tx_delay_loop_shape():
    low = lowered("tx_side", "dly")
    assert ops(low) == ["PULL", "SET", "SET", "DLY", "DLY", "DLY", "OUT", "DLY", "DLY", "JMP",
                        "SET", "DLY", "DLY", "JMP"]
    assert low.prog[1].a == {"dst": "X", "imm": 7}            # loop count doubles as phase lock
    assert low.prog[6].label == "L6" and low.prog[9].a["target"] == "L6"
    assert [i.ob.name for i in low.prog if i.ob] == ["drive tx", "shift tx", "drive tx"]


def test_rx_anchor_and_framing_check():
    low = lowered("rx_side", "dly")
    assert low.prog[0].op == "WAIT" and low.prog[0].a == {"pol": 0, "src": "PIN", "idx": 0}
    assert low.prog[-2].op == "PUSH"
    sets = [i for i in low.prog if i.op == "SET" and i.a["dst"] == "IRQ"]
    assert len(sets) == 1 and sets[0].note == "framing error"


def test_timer_pacing_starts_and_stops_the_bit_timer():
    low = lowered("tx_side", "timer", baud=1843200)
    assert low.timer_q16 == 521
    cmds = [i.a["imm"] for i in low.prog if i.op == "SET" and i.a["dst"] == "CMD"]
    assert cmds == [0x10, 0x20]
    assert sum(1 for i in low.prog if i.op == "WAIT" and i.a["src"] == "EV") == 3


@pytest.mark.parametrize("states, why", [
    ("state i { on host.pull(b) -> f; on host.pull(b) -> f; } state f { seq { drive w = 0; } -> i; }",
     "exactly one 'on' edge"),
    ("state i { on host.pull(b) when b == 1 -> f; } state f { seq { drive w = 0; } -> i; }",
     "'when' and 'do'"),
    ("state i { on host.pull(b) -> f; } state f { seq { drive w = 0; } -> f; }",
     "return to the initial state"),
    ("state i { on host.pull(b) -> f; } state f { seq { wait 1 us; drive w = 0; } -> i; }",
     "'wait' inside seq"),
    ("state i { on host.pull(b) -> f; } state f { seq { drive w = 0; "
     "drive w = 1 within [0 ns, 1 us] after host.pull(b); } -> i; }",
     "a window on any statement but the first"),
    ("state i { on host.pull(b) -> f; } state f { seq { push host b; } -> i; }",
     "without timed statements"),
])
def test_unsupported_shapes_are_r300(states, why):
    sym = front("protocol p { wire w : out; role r : controller uses w { var b : bits[8]; "
                + states + " } }")
    with pytest.raises(TseError) as e:
        frame_shape(sym.roles[0])
    assert e.value.code == "R300" and why in e.value.msg
