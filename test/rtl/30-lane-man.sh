#!/usr/bin/env bash
# Manchester transmit: even half bits, and no stale half bit before a frame.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_man -Itest/rtl
