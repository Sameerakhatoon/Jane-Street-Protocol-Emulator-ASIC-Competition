# SPDX-License-Identifier: Apache-2.0
"""Board code and the datasheet snippets use only names the real Tiny Tapeout SDK, the
board code and the host library have, and the snippets run on the stand-in.

The names come from tests/data/ttboard_sdk_names.txt, extracted from the SDK
source at tt-micropython-firmware commit d485c7a9986336024e35d790568fca33be1c5665.
"""
import ast
import glob
import os
import re
import sys

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
from tse_host import image, model, session  # noqa: E402
from tse_rp2 import board  # noqa: E402
from tse_rp2.board import TseBoard  # noqa: E402

IMPORTS = {"ttboard.demoboard": {"DemoBoard"}, "ttboard.mode": {"RPMode"}}
BOARD_FILES = sorted(glob.glob(os.path.join(ROOT, "host", "tse_rp2", "*.py")))


@pytest.fixture
def world():
    rp2.WORLD.reset(model.Chip(seed=3))
    return rp2.WORLD


def snippets():
    """Every python block of the datasheet page, in order, as one program."""
    text = open(os.path.join(ROOT, "docs", "info.md")).read()
    return "\n".join(re.findall(r"```python\n(.*?)```", text, re.S))


def board_uses(tree):
    """(owner, name) for b.X (the TseBoard), b.session.X and image.X."""
    out = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.Attribute):
            continue
        v = n.value
        if isinstance(v, ast.Name) and v.id in ("b", "image"):
            out.append((v.id, n.attr))
        elif (isinstance(v, ast.Attribute) and v.attr == "session"
              and isinstance(v.value, ast.Name) and v.value.id == "b"):
            out.append(("session", n.attr))
    return out


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


# 3: every name in the datasheet snippets exists in the SDK, the board code or the host library
def test_03_snippet_names():
    tree = ast.parse(snippets())
    assert check(sdk_uses(tree, {"tt"})) == []
    have = {"b": lambda n: n == "session" or hasattr(TseBoard, n),
            "session": lambda n: hasattr(session.Session, n),
            "image": lambda n: hasattr(image, n)}
    uses = board_uses(tree)
    assert len(uses) >= 5 and [u for u in uses if not have[u[0]](u[1])] == []


# 4: the snippets run on the stand-in: the board selects the chip, loads the image,
# starts lanes 0 and 1 and leaves 'A' in lane 0's transmit mailbox
def test_04_snippet_runs(world, tmp_path):
    path = tmp_path / "uart.tsei"
    blocks = [(0x0000, [0] * 8), (0x1019, [0x0100]), (0x1119, [0x0201])]
    path.write_bytes(image.pack(blocks, 60000000, b"uart"))
    code = snippets()
    assert code.count('"/uart.tsei"') == 1
    exec(code.replace('"/uart.tsei"', repr(str(path))), {})
    chip = world.chip
    assert world.selected == board.PROJECT and world.sys_hz == 120000000
    assert chip.loaded and chip.rst_n == 1 and chip.run == 0b11
    assert chip.mbox_tx[0] == 0x41


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
