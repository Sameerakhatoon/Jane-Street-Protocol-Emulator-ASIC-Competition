#!/usr/bin/env bash
# tse_cfg_bank: the staged write data (WD_REG = 1) behaves as the direct one.
. "$(dirname "$0")/lib.sh"
run_tb tb_cfg_stage
