#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Chip-side pin timing: LANE_IN sample delays and uo_out source, the hold
# response rule through the pads, EMIT to trigger pad, TRIG and TICK flags,
# event record timestamps.
. "$(dirname "$0")/lib.sh"
run_tb tb_chip_pins -Itest/rtl
