#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# RUN clears X, Y, T, ISR, OSR, the flags, PINS and PINDIRS.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_run -Itest/rtl
