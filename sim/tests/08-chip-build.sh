#!/usr/bin/env bash
# The full chip (RTL_DIR) builds behind the same harness and runs.
# No program is loaded, so this only proves the pin contract and speed.
. "$(dirname "$0")/lib.sh"

dir="$(make -s --no-print-directory print-RTL_DIR)"
[ -f "${dir}/tse_chip.sv" ] || skip "no chip RTL at '$dir'"
build_top chip
out="$(./build/chip/tse_sim --bench 300000)"
assert_contains "$out" "bench: top chip, 300000 cycles" "bench line"
pass "$(sed -n 's/.* = \([0-9]* cycles\/s\).*/\1/p' <<<"$out")"
