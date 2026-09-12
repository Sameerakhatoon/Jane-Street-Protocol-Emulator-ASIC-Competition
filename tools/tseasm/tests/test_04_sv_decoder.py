"""The generated SystemVerilog decoder agrees with the Python decoder on all 65,536 words (Icarus)."""
import shutil
import subprocess

import pytest

from tools.tseasm import isa

TB = """
module tb;
  import tse_isa_pkg::*;
  initial begin
    for (int i = 0; i < 65536; i++)
      $display("%0d %0d", tse_decode(16'(i)), tse_legal(16'(i)));
    $finish;
  end
endmodule
"""


# 1: same class and legality for every word
@pytest.mark.skipif(not shutil.which("iverilog"), reason="needs iverilog")
def test_01_sv_matches_python(tmp_path):
    (tmp_path / "tb.sv").write_text(TB)
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp), "src/tse_isa_pkg.sv", str(tmp_path / "tb.sv")],
                   check=True)
    out = subprocess.run(["vvp", "-n", str(vvp)], check=True, capture_output=True, text=True).stdout
    rows = [line.split() for line in out.splitlines() if line and line[0].isdigit()]
    assert len(rows) == 65536
    names = list(isa.INSTRS)
    for w, (cls, legal) in enumerate(rows):
        t = next((n for n, i in isa.INSTRS.items() if w & i["mask"] == i["value"]), None)
        assert int(cls) == (names.index(t) if t else len(names)), hex(w)
        assert int(legal) == isa.is_legal(w), hex(w)
