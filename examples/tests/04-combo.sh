#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Both lanes at once (UART on lane 0, I2C on lane 1): the UART judge, the
# UART timing cross-check and the I2C judge must all pass.
. "$(dirname "$0")/lib.sh"

bash examples/combo/run.sh "$OUT" >"$OUT/run.log" 2>&1 || { tail -30 "$OUT/run.log" >&2; fail "see $OUT/run.log"; }
pass "$(tail -1 "$OUT/run.log")"
