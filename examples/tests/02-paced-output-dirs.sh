#!/usr/bin/env bash
# The paced-frame lowering sets the output pins to their idle level and turns
# their pads on before the anchor, so the UART transmitter image needs no
# prologue from the host: the demo helper finds SET PINDIRS and starts at 0.
. "$(dirname "$0")/lib.sh"

python3 -m tools.tsec build specs/uart_tx.tse --emit img,json -o "$OUT" >"$OUT/tsec.log" 2>&1 \
    || { cat "$OUT/tsec.log" >&2; fail "tsec build"; }
res="$(python3 - "$OUT" <<"PY"
import sys
sys.path.insert(0, "examples")
import tse_demo as td
role, words, regs, pins = td.read_tsec(sys.argv[1], "uart_tx")[0]
prog, start = td.output_prologue(words, pins)
print("sets" if start == 0 and prog == words else "missing")
PY
)"
[ "$res" = "sets" ] || fail "the program does not set its output direction"
pass "SET PINDIRS in the program, no host prologue"
