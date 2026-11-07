#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# WAIT: pin-change sources, alternate pin change, flags cleared at dispatch,
# TIMER flag acknowledge, successor fetched while waiting.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_wait -Itest/rtl
