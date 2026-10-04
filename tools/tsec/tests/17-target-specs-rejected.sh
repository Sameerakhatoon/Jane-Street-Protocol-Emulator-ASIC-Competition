#!/usr/bin/env bash
# The SPI and I2C target specs as written are rejected for timing only, with the exact
# obligation, margin and fix, and no image (rule R1).
. "$(dirname "$0")/lib.sh"
st=0
tsec build specs/spi_target.tse -o "$TMP/spi" > /dev/null 2> "$TMP/err" || st=$?
err="$(cat "$TMP/err")"
assert_eq 2 "$st" "spi exit status"
assert_contains "$err" "response window not met in role 'tgt': shift miso late [R311]" "first_bit"
assert_contains "$err" "achieved   : 66.7 ns .. 158.3 ns" "first_bit achieved"
assert_contains "$err" "late by    : 63.3 ns" "first_bit late by"
assert_contains "$err" "fix        : t_css >= 164 ns (139 ns with ideal pads)" "first_bit fix"
assert_contains "$err" "fix        : f_sck <= 3.89 MHz," "mosi fix"
[ ! -e "$TMP/spi/spi_target.img" ] || fail "spi wrote an image"
st=0
tsec build specs/i2c_target.tse -o "$TMP/i2c" > /dev/null 2> "$TMP/err" || st=$?
err="$(cat "$TMP/err")"
assert_eq 2 "$st" "i2c exit status"
assert_contains "$err" "the wait that detects stop (abort) starts 12 cycles after stop may already be visible [R314]" "stop"
assert_contains "$err" "add a datasheet rule start -> stop >= 216.7 ns" "stop fix"
assert_eq 1 "$(grep -c ': error: ' "$TMP/err")" "i2c rejections"
[ ! -e "$TMP/i2c/i2c_target.img" ] || fail "i2c wrote an image"
pass
