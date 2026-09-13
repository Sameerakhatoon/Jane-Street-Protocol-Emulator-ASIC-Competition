#!/usr/bin/env bash
# build specs/uart.tse --lat 2: both roles accepted; listing and obligations golden.
. "$(dirname "$0")/lib.sh"
golden uart-lat2 tsec build specs/uart.tse --lat 2
assert_eq 0 "$status" "exit status"
pass
