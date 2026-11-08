#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The SPI target as written: between two bytes the lane tests cs_n, pushes and jumps back, so
# at 4 MHz with bytes back to back the first bit of the next byte is sampled too late.
# The I2C target is rejected for timing only, with the obligation and fix, and no image (R1).
. "$(dirname "$0")/lib.sh"
st=0
tsec build specs/spi_target.tse -o "$TMP/spi" > /dev/null 2> "$TMP/err" || st=$?
err="$(cat "$TMP/err")"
assert_eq 2 "$st" "spi exit status"
assert_contains "$err" "shift mosi late [R311]" "byte gap"
assert_contains "$err" "f_sck <= 2.8 MHz (3.26 MHz with ideal pads)" "spi fix"
[ ! -e "$TMP/spi/spi_target.img" ] || fail "spi wrote an image"
st=0
tsec build specs/i2c_target.tse -o "$TMP/i2c" > /dev/null 2> "$TMP/err" || st=$?
err="$(cat "$TMP/err")"
assert_eq 2 "$st" "i2c exit status"
assert_contains "$err" "the wait that detects stop (abort) starts 12 cycles after stop may already be visible [R314]" "stop"
assert_contains "$err" "add a datasheet rule start -> stop >= 216.7 ns" "stop fix"
assert_contains "$err" "armed before the earliest stop after fall(sda)" "re-arm after a start"
assert_eq 5 "$(grep -c ': error: ' "$TMP/err")" "i2c rejections"
[ ! -e "$TMP/i2c/i2c_target.img" ] || fail "i2c wrote an image"
pass
