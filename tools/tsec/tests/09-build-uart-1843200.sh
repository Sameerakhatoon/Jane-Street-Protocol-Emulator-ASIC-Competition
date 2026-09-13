#!/usr/bin/env bash
# build specs/uart.tse -D baud=1843200: both roles accepted; listing and obligations golden.
. "$(dirname "$0")/lib.sh"
golden uart-1843200 tsec build specs/uart.tse -D baud=1843200
assert_eq 0 "$status" "exit status"
pass
