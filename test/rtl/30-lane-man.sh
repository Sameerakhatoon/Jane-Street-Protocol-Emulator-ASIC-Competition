#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Manchester transmit: even half bits, and no stale half bit before a frame.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_man -Itest/rtl
