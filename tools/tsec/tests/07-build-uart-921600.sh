#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# build specs/uart.tse -D baud=921600: both roles accepted; listing and obligations golden.
. "$(dirname "$0")/lib.sh"
golden uart-921600 tsec build specs/uart.tse -D baud=921600
assert_eq 0 "$status" "exit status"
pass
