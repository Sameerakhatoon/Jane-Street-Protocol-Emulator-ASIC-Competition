#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Rule chaining, SET with a write mask, illegal-word halt.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_rules -Itest/rtl
