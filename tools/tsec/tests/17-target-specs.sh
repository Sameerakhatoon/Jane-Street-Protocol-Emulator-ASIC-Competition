#!/usr/bin/env bash
# The SPI target as written: between two bytes the lane pushes and waits for SCK low again,
# which at 4 MHz comes after the next rise, so the first bit of the next byte would be lost.
# The I2C target is rejected for timing only, with the obligation and fix, and no image (R1).
. "$(dirname "$0")/lib.sh"
st=0
tsec build specs/spi_target.tse -o "$TMP/spi" > /dev/null 2> "$TMP/err" || st=$?
err="$(cat "$TMP/err")"
assert_eq 2 "$st" "spi exit status"
assert_contains "$err" "reaching the wait before the earliest rise(sck) after fall(sck)" "byte gap"
assert_contains "$err" "f_sck <= 1.99 MHz" "spi fix"
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
