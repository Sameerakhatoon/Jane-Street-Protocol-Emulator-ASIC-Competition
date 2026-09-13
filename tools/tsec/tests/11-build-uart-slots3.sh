#!/usr/bin/env bash
# build specs/uart.tse --slots 3: both roles accepted; listing and obligations golden.
. "$(dirname "$0")/lib.sh"
golden uart-slots3 tsec build specs/uart.tse --slots 3
assert_eq 0 "$status" "exit status"
pass
