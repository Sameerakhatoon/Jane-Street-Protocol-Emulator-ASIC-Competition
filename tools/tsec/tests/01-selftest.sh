#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The ISA tables and the cycle model reproduce the published encodings and cycle counts.
. "$(dirname "$0")/lib.sh"
golden selftest tsec selftest
assert_eq 0 "$status" "exit status"
pass
