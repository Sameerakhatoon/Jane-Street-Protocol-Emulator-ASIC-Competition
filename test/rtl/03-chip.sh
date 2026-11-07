#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Whole chip through the Tiny Tapeout pins: self-test, load, CRC check, readback,
# commit, a UART byte from lane 0, an event read over the runtime SPI, run key
# clear. Runs the latch configuration and the flop variant.
. "$(dirname "$0")/lib.sh"
fail=0
for c in 1 0; do
    run_tb tb_tse_chip -Itest/rtl -DLATCH_TB=$c || fail=1
done
exit $fail
