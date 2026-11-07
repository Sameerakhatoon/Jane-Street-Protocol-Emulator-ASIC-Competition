#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The data window: an array indexed from data (LDW X, STW X), a variable that outlives its
# shift register (stored and loaded again), a mode param choosing events at item level,
# a constant wider than an immediate, parity, and a loop count from data.
. "$(dirname "$0")/lib.sh"
st=0
out="$(tsec build $TESTS/targets/regfile.tse --emit all -o "$TMP/rf")" || st=$?
assert_eq 0 "$st" "regfile exit status"
lst="$(cat "$TMP/rf/regfile.lst")"
assert_contains "$lst" "STW   X" "indexed store"
assert_contains "$lst" "LDW   X" "indexed load"
assert_contains "$lst" "OUT   X, 3" "index of words[8] at word 0"
assert_contains "$out" "data window word 0 to 7: array regs" "array map"
assert_contains "$out" "data window word 8: variable cmd" "home of cmd"
json="$(cat "$TMP/rf/regfile.json")"
assert_contains "$json" '"data_window"' "window in the image JSON"
assert_contains "$json" '"words": 128' "window size"
st=0
tsec build $TESTS/targets/regfile.tse -D mode=1 -o "$TMP/rf1" > "$TMP/rf1.out" || st=$?
assert_eq 0 "$st" "regfile mode 1 exit status"
assert_contains "$(cat "$TMP/rf1.out")" "after cap          " "mode 1 builds with its own events"
st=0
out="$(tsec build $TESTS/targets/framer.tse -o "$TMP/fr")" || st=$?
assert_eq 0 "$st" "framer exit status"
assert_contains "$out" "parity of data" "parity loop"
assert_contains "$out" "JMP   XDEC, loop" "loop count from data"
assert_contains "$out" "constant 0xA5C33C, the host loads 0xA5C33C before RUN" "wide constant"
assert_contains "$(cat "$TMP/fr/framer.json")" '"0x00A5C33C"' "constant in the load list"
pass
