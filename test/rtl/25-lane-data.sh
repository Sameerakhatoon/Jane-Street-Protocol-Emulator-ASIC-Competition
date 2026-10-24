#!/usr/bin/env bash
# LDW and STW: exact data-slot durations in all 16 phases, lanes 0 and 1.
. "$(dirname "$0")/lib.sh"
fail=0
for l in 0 1; do
    run_tb tb_lane_data -Itest/rtl -DLANE_TB=$l || fail=1
done
exit $fail
