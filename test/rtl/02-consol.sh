#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Sequencer and response rules: two-entry fetch buffer, WAIT alternate
# condition, CSRW config cost, pin-edge and bit-mode response rules.
. "$(dirname "$0")/lib.sh"
run_tb tb_consol
