#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The slot period belongs to the ISA table: --slots with any other value is a command-line error.
. "$(dirname "$0")/lib.sh"
st=0
err="$(tsec build specs/uart.tse --slots 3 2>&1 > /dev/null)" || st=$?
assert_eq 1 "$st" "exit status"
assert_contains "$err" "tsec: --slots 3: the slot period is fixed by the ISA table at 4" "message"
st=0
tsec build specs/uart.tse --slots 4 > /dev/null || st=$?
assert_eq 0 "$st" "the table value is accepted"
pass
