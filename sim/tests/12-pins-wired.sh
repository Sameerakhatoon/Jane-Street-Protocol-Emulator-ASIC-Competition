#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# bridge/pins_gen.h matches the pinout tables in test/info_uart.yaml and info.yaml,
# and the WiredAnd resolver and pin names pass their unit checks.
. "$(dirname "$0")/lib.sh"

python3 gen_pins.py -o "$TMP/pins_gen.h" || fail "gen_pins.py"
diff -u bridge/pins_gen.h "$TMP/pins_gen.h" >"$TMP/diff" \
    || fail "bridge/pins_gen.h is stale, run make pins: $(head -20 "$TMP/diff")"
make -s --no-print-directory build/unit/unit_wired >"$TMP/build" 2>&1 || fail "$(tail -20 "$TMP/build")"
out="$(./build/unit/unit_wired)" || fail "$(grep FAIL <<<"$out")"
pass "$(grep -c '^ok' <<<"$out") checks"
