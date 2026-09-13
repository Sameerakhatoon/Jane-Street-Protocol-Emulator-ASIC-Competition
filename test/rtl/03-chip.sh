#!/usr/bin/env bash
# Whole chip through the Tiny Tapeout pins: self-test, load, CRC check, readback,
# commit, a UART byte from lane 0, an event read over the runtime SPI, run key
# clear. Runs the 6x4 build (3 lanes, latch config) and the flop and 4-lane variants.
. "$(dirname "$0")/lib.sh"
fail=0
for v in "3 1" "3 0" "4 1"; do
    set -- $v
    run_tb tb_tse_chip -DLANES_TB=$1 -DLATCH_TB=$2 || fail=1
done
exit $fail
