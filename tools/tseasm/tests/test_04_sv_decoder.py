"""The generated SystemVerilog packages agree with the Python tables (Icarus): decoder on all
65,536 words, lane register class and word on all 256 addresses, chip bank and word on the host map."""
import shutil
import subprocess

import pytest

from tools.tseasm import isa, regs

TB = """
module tb;
  import tse_isa_pkg::*;
  initial begin
    for (int i = 0; i < 65536; i++)
      $display("%0d %0d %0d %0d %0d %0d", tse_decode(16'(i)), tse_legal(16'(i)),
               tse_delay(16'(i)), tse_may_wait(16'(i)), tse_dslot(16'(i)), tse_cfgword(16'(i)));
    $finish;
  end
endmodule
"""


# 1: same class, legality and cost terms for every word
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
    dslots = [None, "load", "store"]
    for w, row in enumerate(rows):
        cls, legal, delay, wait, dslot, cfgword = map(int, row)
        t = next((n for n, i in isa.INSTRS.items() if w & i["mask"] == i["value"]), None)
        assert cls == (names.index(t) if t else len(names)), hex(w)
        assert legal == isa.is_legal(w), hex(w)
        ins = isa.decode(w)
        if ins is None:
            continue
        c = isa.cost(ins)
        extra = cfgword if ins.name == "CSRW" and regs.is_cfg(ins.fields["addr"]) else 0
        assert (1 + delay + extra, bool(wait), dslots[dslot]) == tuple(c), hex(w)


TB_REGS = """
module tb;
  import tse_regs_pkg::*;
  chip_cfg_t c;
  initial begin
    for (int i = 0; i < 256; i++)
      $display("L %0d %0d %0d", i, lane_reg_class(8'(i)), lane_reg_word(8'(i)));
    for (int i = 16'h2000; i < 16'h4100; i++) begin
      c = chip_reg_cfg(16'(i));
      if (c.hit) $display("C %0d %0d %0d", i, c.bank, c.word);
    end
    $finish;
  end
endmodule
"""


# 2: lane_reg_class, lane_reg_word and chip_reg_cfg match regs.py
@pytest.mark.skipif(not shutil.which("iverilog"), reason="needs iverilog")
def test_02_sv_regs_match_python(tmp_path):
    (tmp_path / "tb.sv").write_text(TB_REGS)
    vvp = tmp_path / "tb.vvp"
    subprocess.run(["iverilog", "-g2012", "-o", str(vvp), "src/tse_regs_pkg.sv", str(tmp_path / "tb.sv")],
                   check=True)
    out = subprocess.run(["vvp", "-n", str(vvp)], check=True, capture_output=True, text=True).stdout
    classes = ["NONE", "L", "F", "P", "R"]
    chip, lanes = {}, 0
    for line in out.splitlines():
        f = line.split()
        if f and f[0] == "L":
            a, cls, word = map(int, f[1:])
            n = regs.lane_reg(a)
            assert classes[cls] == (regs.LANE[n]["class"] if n else "NONE"), hex(a)
            assert word == (regs.LANE[n].get("word", 0) if n else 0), hex(a)
            lanes += 1
        elif f and f[0] == "C":
            a, bank, word = map(int, f[1:])
            chip[a] = (regs.CHIP_BANKS[bank], word)
    assert lanes == 256 and chip == regs.CHIP_CFG
