#!/usr/bin/env bash
# build specs/uart.tse -D baud=921600 --entries 1: both roles accepted; listing and obligations golden.
. "$(dirname "$0")/lib.sh"
golden uart-921600-entries1 tsec build specs/uart.tse -D baud=921600 --entries 1
assert_eq 0 "$status" "exit status"
pass
