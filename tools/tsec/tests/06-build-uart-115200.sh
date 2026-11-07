#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# build specs/uart.tse with the defaults: both roles accepted; listing and obligations golden.
. "$(dirname "$0")/lib.sh"
golden uart-115200 tsec build specs/uart.tse
assert_eq 0 "$status" "exit status"
pass
