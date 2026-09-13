#!/usr/bin/env bash
# tse_slot_arb non-interference with 3 and 4 lanes.
. "$(dirname "$0")/lib.sh"
fail=0
for l in 3 4; do
    run_tb tb_slot_arb -DLANES_TB=$l || fail=1
done
exit $fail
