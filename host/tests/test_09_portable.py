"""The code that must run on MicroPython stays inside its subset; generated tables are current."""
import ast
import os
import subprocess
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(HERE)
ROOT = os.path.dirname(HOST)

PORTABLE = ["tse_host/__init__.py", "tse_host/frames.py", "tse_host/image.py", "tse_host/spi.py",
            "tse_host/link.py", "tse_host/session.py", "tse_host/regs_map.py",
            "tse_rp2/__init__.py", "tse_rp2/bytebus.py", "tse_rp2/board.py", "tse_rp2/regs.py",
            "tse_rp2/port.py", "tse_rp2/tse_asm.py", "tse_rp2/tse_asm_table.py"]
CPYTHON_ONLY = {"typing", "dataclasses", "re", "enum", "pathlib", "zlib", "subprocess", "json",
                "argparse", "os", "functools", "itertools", "collections", "random"}


# 1: no f-strings, no CPython-only modules, no keyword-only or annotated arguments
@pytest.mark.parametrize("rel", PORTABLE)
def test_01_micropython_subset(rel):
    tree = ast.parse(open(os.path.join(HOST, rel)).read())
    for node in ast.walk(tree):
        assert not isinstance(node, ast.JoinedStr), "%s:%d f-string" % (rel, node.lineno)
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for n in names:
                assert n.split(".")[0] not in CPYTHON_ONLY, "%s imports %s" % (rel, n)
        if isinstance(node, ast.arguments):
            assert not node.kwonlyargs and not any(a.annotation for a in node.args), rel
        assert not isinstance(node, (ast.AnnAssign, ast.Match)), rel


# 2: the exported tables match a fresh export
def test_02_tables_current(tmp_path):
    for rel in ("host/tse_host/regs_map.py", "host/tse_rp2/tse_asm_table.py"):
        with open(os.path.join(ROOT, rel)) as fh:
            before = fh.read()
        subprocess.run([sys.executable, os.path.join(HOST, "gen_tables.py")], check=True)
        with open(os.path.join(ROOT, rel)) as fh:
            assert fh.read() == before, "%s is stale: run host/gen_tables.py" % rel
