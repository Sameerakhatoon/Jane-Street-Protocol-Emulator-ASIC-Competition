#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# tse_cfg_word: latch and flop versions agree under the three-cycle write contract.
. "$(dirname "$0")/lib.sh"
run_tb tb_cfg_word
