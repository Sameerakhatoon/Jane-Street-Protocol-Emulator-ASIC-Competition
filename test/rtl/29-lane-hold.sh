#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Response rule hold: the first bit of an SPI target from a CS edge, released
# by the program write to txpin.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_hold -Itest/rtl
