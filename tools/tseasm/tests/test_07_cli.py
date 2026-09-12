"""Command line: assemble, listing, SRAM words, disassemble, error exit."""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent


def run(*args):
    return subprocess.run([sys.executable, "-m", "tools.tseasm", *map(str, args)],
                          capture_output=True, text=True)


# 1: assemble the UART example to the expected hex and a listing
def test_01_assemble(tmp_path):
    out, lst = tmp_path / "uart.hex", tmp_path / "uart.lst"
    r = run(HERE / "uart_tx.s", "-o", out, "-l", lst)
    assert r.returncode == 0, r.stderr
    assert out.read_text() == (HERE / "uart_tx.hex").read_text()
    assert lst.read_text().splitlines()[6].split()[:2] == ["6", "8806"]


# 2: SRAM words, then disassemble them and assemble the result again
def test_02_words_and_disasm(tmp_path):
    out, src = tmp_path / "uart32.hex", tmp_path / "back.s"
    assert run(HERE / "uart_tx.s", "-w", "-o", out).returncode == 0
    assert out.read_text().split()[0] == "41FD4011"
    assert run("-d", out, "-o", src).returncode == 0
    r = run(src)
    assert r.returncode == 0 and r.stdout == (HERE / "uart_tx.hex").read_text()


# 3: an error exits 1 and names the file and line
def test_03_error(tmp_path):
    bad = tmp_path / "bad.s"
    bad.write_text("SET X, 1\nJMP NOWHERE, 0\n")
    r = run(bad)
    assert r.returncode == 1 and "bad.s: line 2:" in r.stderr
