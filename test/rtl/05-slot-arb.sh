#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# tse_slot_arb non-interference.
. "$(dirname "$0")/lib.sh"
fail=0
run_tb tb_slot_arb || fail=1
exit $fail
