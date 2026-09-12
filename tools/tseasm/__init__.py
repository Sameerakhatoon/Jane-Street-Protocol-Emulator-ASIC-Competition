"""tseasm: TSE lane assembler, disassembler and the ISA and register tables.

    from tools.tseasm import isa, regs          # tables and encode/decode/cost
    from tools.tseasm import assemble, disasm   # text to words and back
"""
from . import isa, regs
from .asm import AsmError, Program, assemble, hex16, hex32, listing
from .disasm import disasm, disasm_word

__all__ = ["isa", "regs", "AsmError", "Program", "assemble", "hex16", "hex32",
           "listing", "disasm", "disasm_word"]
