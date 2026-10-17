#!/usr/bin/env bash
# Lane registers by name: CSRW to every class-L register lands in its bank
# word, costs by register class, LANE_CFG side-set and jump pin fields.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_regs -Itest/rtl
