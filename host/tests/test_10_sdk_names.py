# SPDX-License-Identifier: Apache-2.0
"""Board code and the datasheet snippet use only names the real Tiny Tapeout SDK has.

The names come from tests/data/ttboard_sdk_names.txt, extracted from the SDK
source at tt-micropython-firmware commit d485c7a9986336024e35d790568fca33be1c5665.
"""
import ast
import glob
import os
import re
import sys
import types

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
SHIM = os.path.join(HERE, "shim")
if SHIM not in sys.path:
    sys.path.insert(0, SHIM)

import rp2  # noqa: E402  (the shim)
from machine import Pin  # noqa: E402
from ttboard import demoboard  # noqa: E402
from ttboard.demoboard import SDK, DemoBoard  # noqa: E402
from ttboard.mode import RPMode  # noqa: E402
from tse_host import model  # noqa: E402
from tse_rp2 import board  # noqa: E402

IMPORTS = {"ttboard.demoboard": {"DemoBoard"}, "ttboard.mode": {"RPMode"}}
BOARD_FILES = sorted(glob.glob(os.path.join(ROOT, "host", "tse_rp2", "*.py")))


@pytest.fixture
def world():
    rp2.WORLD.reset(model.Chip(seed=3))
    return rp2.WORLD


def snippet():
    text = open(os.path.join(ROOT, "docs", "info.md")).read()
    return re.search(r"```python\n(.*?)```", text, re.S).group(1)


def sdk_uses(tree, roots):
    """(class, name) for imports from ttboard, RPMode.X and <root>.X where root is the board."""
    out = []
    for n in ast.walk(tree):
        if isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("ttboard"):
            out += [("import " + n.module, a.name) for a in n.names]
        elif isinstance(n, ast.Attribute):
            v = n.value
            if isinstance(v, ast.Name) and v.id == "RPMode":
                out.append(("RPMode", n.attr))
            elif isinstance(v, ast.Name) and v.id in roots:
                out.append(("DemoBoard", n.attr))
            elif isinstance(v, ast.Attribute) and v.attr in roots:
                out.append(("DemoBoard", n.attr))
    return out


def check(uses):
    bad = []
    for cls, name in uses:
        known = IMPORTS.get(cls[7:], set()) if cls.startswith("import ") else SDK[cls]
        if name not in known:
            bad.append((cls, name))
    return bad


# 1: the stand-in itself defines no public name the SDK lacks
def test_01_shim_names():
    for cls, sdk in ((DemoBoard, "DemoBoard"), (demoboard._Project, "Design"),
                     (demoboard._ResetPin, "StandardPin"), (RPMode, "RPMode")):
        own = {k for k in vars(cls) if not k.startswith("_") and k != "SDK_CLASS"}
        assert own <= SDK[sdk], (sdk, own - SDK[sdk])


# 2: every SDK name in tse_rp2 exists in the SDK
@pytest.mark.parametrize("path", BOARD_FILES, ids=os.path.basename)
def test_02_board_files(path):
    tree = ast.parse(open(path).read(), path)
    assert check(sdk_uses(tree, {"tt"})) == []


# 3: every SDK name in the datasheet snippet exists in the SDK
def test_03_snippet_names():
    uses = sdk_uses(ast.parse(snippet()), {"tt"})
    assert len(uses) > 10 and check(uses) == []


# 4: the snippet runs on the stand-in and leaves 'A' on ui_in with uio[0] pulsed
def test_04_snippet_runs(world, monkeypatch):
    seen = []
    real = world.chip.set_inputs

    def spy(ui_in=None, uio_in=None, rst_n=None):
        seen.append((ui_in, uio_in & 1, rst_n))
        real(ui_in=ui_in, uio_in=uio_in, rst_n=rst_n)
    world.chip.set_inputs = spy
    fake_time = types.SimpleNamespace(sleep_ms=lambda ms: rp2.sleep_us(ms * 1000))
    monkeypatch.setitem(sys.modules, "time", fake_time)
    exec(snippet(), {})
    monkeypatch.undo()
    assert world.selected == "tt_um_tse_sk" and world.sys_hz == 120000000
    assert world.clk_div == 2 and world.owner[25] == world.owner[26] == "cpu"
    assert (0x41, 1, 1) in seen and seen[-1][:2] == (0x41, 0)


# 5: a name the SDK lacks fails here as it would on the board
def test_05_unknown_names_refused(world):
    tt = DemoBoard.get()
    for bad in ("clock_project_pwm", "uio_oe", "project_reset", "ui_out"):
        with pytest.raises(AttributeError):
            getattr(tt, bad)
    with pytest.raises(AttributeError):
        tt.ui_in.byte_value = 1
    with pytest.raises(AttributeError):
        tt.rst_n.toggle()


# 6: the board selects the project by the full chip's top module, the name the
# shuttle index uses
def test_06_project_name():
    text = open(os.path.join(ROOT, "info.yaml")).read()
    assert board.PROJECT == re.search(r'top_module:\s*"([^"]+)"', text).group(1)


# 7: PIO0's window cannot move once a program is loaded, and the board copes
def test_07_pio_window_locked(world):
    world.sys_hz = 120000000
    board.set_pio_window()
    rp2.StateMachine(0, board.bytebus.loader_tx, freq=30000000, out_base=Pin(17))
    with pytest.raises(OSError):
        rp2.PIO(0).gpio_base(Pin(16))
    board.set_pio_window()
    assert rp2.PIO(0).gpio_base() == 16


# 8: rst_n held low (button pressed) makes the release fail instead of loading blind
def test_08_reset_stuck(world):
    tt = DemoBoard.get()
    link = board.BoardLink(tt)
    tt.reset_project(True)
    tt.reset_project = lambda put_in_reset: None
    with pytest.raises(RuntimeError):
        link.release_reset()
