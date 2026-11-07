#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Lane STATUS layout, program and host reads, illegal halt fields.
. "$(dirname "$0")/lib.sh"
run_tb tb_lane_status -Itest/rtl
