#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Event unit trigger and log, pin delay taps, timer period, config bank write and clear.
. "$(dirname "$0")/lib.sh"
run_tb tb_small
