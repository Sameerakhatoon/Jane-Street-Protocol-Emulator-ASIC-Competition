#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Paced sequences in state machines: the JTAG probe (two shifted-out pins, TCK on side-set,
# samples issued after the next launch) places every action within its tolerance once the
# pads are characterised; with the default pad budget the skew between pins is rejected.
. "$(dirname "$0")/lib.sh"
st=0
out="$(tsec build specs/jtag_probe.tse --pad-skew 0 -o "$TMP/jtag")" || st=$?
assert_eq 0 "$st" "jtag exit status with zero skew"
assert_contains "$out" "shift tdo -> cap lsb 1 for t_rd ~ t_smp  placement error 0.20 of 0.59 ticks  ok" "tdo"
assert_contains "$(cat "$TMP/jtag/jtag_probe.json")" '"LANE_CFG": "0x00000060"' "side-set on pin 2"
assert_contains "$(cat "$TMP/jtag/jtag_probe.lst")" "OUT   PINS, 2" "two pins shifted"
st=0
err="$(tsec build specs/jtag_probe.tse 2>&1 > /dev/null)" || st=$?
assert_eq 2 "$st" "jtag exit status with the default pad budget"
assert_contains "$err" "pads characterised with a skew at or below" "skew fix"
st=0
out="$(tsec build specs/spi_controller_mode0.tse --pad-skew 0)" || st=$?
assert_eq 0 "$st" "spi controller exit status"
assert_contains "$out" "unrolled loops" "spi strategy"
pass
