#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Opcode 7 split: DLY, TMR, SIG and EMIT durations and outputs, SET IRQ.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_op7 -Itest/rtl
