#!/usr/bin/env bash
# A line typed on the simulator's UART pty comes back through the compiled
# receive and transmit lanes; both lines are judged.
. "$(dirname "$0")/lib.sh"

bash examples/pty/run.sh "$OUT" >"$OUT/run.log" 2>&1 || { tail -30 "$OUT/run.log" >&2; fail "see $OUT/run.log"; }
pass "$(tail -1 "$OUT/run.log")"
