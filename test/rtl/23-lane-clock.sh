#!/usr/bin/env bash
# Clock C, guard thresholds, JMP GUARD, CLOCK and TS reads, WAIT CHIP, SYNCED.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_clock -Itest/rtl
