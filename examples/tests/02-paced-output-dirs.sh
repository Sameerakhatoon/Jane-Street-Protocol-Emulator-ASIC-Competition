#!/usr/bin/env bash
# Expected failure: the paced-frame lowering of tsec never sets the pin
# direction of an output wire, so the UART transmitter would leave its pad
# idle. The examples append a prologue (SET PINS idle, SET PINDIRS, JMP 0)
# and start the lane there. This test turns into a failure the day tsec
# emits the direction itself; then drop the prologue and this test.
. "$(dirname "$0")/lib.sh"

python3 -m tools.tsec build specs/uart_tx.tse --emit img,json -o "$OUT" >"$OUT/tsec.log" 2>&1 \
    || { cat "$OUT/tsec.log" >&2; fail "tsec build"; }
res="$(python3 - "$OUT" <<"PY"
import sys
sys.path.insert(0, "examples")
import tse_demo as td
role, words, regs, pins = td.read_tsec(sys.argv[1], "uart_tx")[0]
prog, start = td.output_prologue(words, pins)
print("sets" if start == 0 else "missing " + " ".join("%04x" % w for w in prog[start:]))
PY
)"
case "$res" in
    "missing 4011 4111 0000") pass "expected failure: no SET PINDIRS in the program" ;;
    missing*) fail "prologue changed: $res" ;;
    sets) fail "tsec now sets the output direction: remove the prologue and this test" ;;
    *) fail "unexpected: $res" ;;
esac
