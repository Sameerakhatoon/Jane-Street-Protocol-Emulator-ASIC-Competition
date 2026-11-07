# SPDX-License-Identifier: Apache-2.0
"""Command line: python3 -m tools.tseasm prog.s -o prog.hex [-l prog.lst] [-w]
                python3 -m tools.tseasm -d prog.hex [-o prog.s]

Hex files hold one word per line: 4 hex digits for an instruction, or 8 for
an SRAM word (two instructions, the even address in bits 15:0).
"""
import argparse
import sys

from .asm import AsmError, assemble, hex16, hex32, listing
from .disasm import disasm


def read_hex(text):
    words = []
    for line in text.splitlines():
        for tok in line.split(";", 1)[0].split("#", 1)[0].split():
            v = int(tok, 16)
            if len(tok) > 4:
                words += [v & 0xFFFF, v >> 16]
            else:
                words.append(v)
    return words


def write(path, text):
    if path in (None, "-"):
        sys.stdout.write(text)
    else:
        with open(path, "w") as fh:
            fh.write(text)


def main(argv=None):
    ap = argparse.ArgumentParser(prog="python3 -m tools.tseasm",
                                 description="TSE lane assembler and disassembler")
    ap.add_argument("input")
    ap.add_argument("-o", dest="output", help="output file (default stdout)")
    ap.add_argument("-l", dest="listing", help="also write a listing")
    ap.add_argument("-d", dest="disasm", action="store_true", help="disassemble a hex file")
    ap.add_argument("-w", dest="words", action="store_true", help="write 32-bit SRAM words")
    args = ap.parse_args(argv)
    with open(args.input) as fh:
        text = fh.read()
    if args.disasm:
        write(args.output, disasm(read_hex(text)))
        return 0
    try:
        prog = assemble(text)
    except AsmError as e:
        print(f"{args.input}: {e}", file=sys.stderr)
        return 1
    write(args.output, hex32(prog.words) if args.words else hex16(prog.words))
    if args.listing:
        write(args.listing, listing(prog))
    return 0


if __name__ == "__main__":
    sys.exit(main())
