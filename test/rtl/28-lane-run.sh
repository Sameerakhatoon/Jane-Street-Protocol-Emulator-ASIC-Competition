#!/usr/bin/env bash
# RUN clears X, Y, T, ISR, OSR, the flags, PINS and PINDIRS.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_run -Itest/rtl
