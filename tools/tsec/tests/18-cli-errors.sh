#!/usr/bin/env bash
# Command-line mistakes: overriding a const, an unknown param, a non-number, a missing file.
. "$(dirname "$0")/lib.sh"
st=0
err="$(tsec build specs/i2c_target.tse -D t_vd=1e-6 2>&1 > /dev/null)" || st=$?
assert_eq 1 "$st" "const override status"
assert_contains "$err" "'t_vd' is a const and cannot be overridden with -D [E106]" "E106"
st=0
err="$(tsec build specs/uart.tse -D baudrate=9600 2>&1 > /dev/null)" || st=$?
assert_eq 1 "$st" "unknown param status"
assert_contains "$err" "specs/uart.tse: error: -D baudrate: no param named 'baudrate' [E107]" "E107"
st=0
err="$(tsec build specs/uart.tse -D baud=fast 2>&1 > /dev/null)" || st=$?
assert_eq 1 "$st" "non-number status"
assert_contains "$err" "-D baud=fast: not a number [E108]" "E108"
st=0
err="$(tsec check no/such/file.tse 2>&1)" || st=$?
assert_eq 1 "$st" "missing file status"
assert_contains "$err" "cannot read no/such/file.tse" "missing file"
pass
