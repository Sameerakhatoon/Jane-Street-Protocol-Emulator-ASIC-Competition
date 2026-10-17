#!/usr/bin/env bash
# Opcode 7 split: DLY, TMR, SIG and EMIT durations and outputs, SET IRQ.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_op7 -Itest/rtl
