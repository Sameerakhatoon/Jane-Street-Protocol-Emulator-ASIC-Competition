#!/usr/bin/env bash
# The SPI target as written: the sequencer alone misses the first MISO bit after CS falls,
# so response rule 0 drives it in pin-edge mode with hold (until the program writes MISO).
# The I2C target is rejected for timing only, with the obligation and fix, and no image (R1).
. "$(dirname "$0")/lib.sh"
st=0
out="$(tsec build specs/spi_target.tse -o "$TMP/spi")" || st=$?
assert_eq 0 "$st" "spi exit status"
assert_contains "$out" "33.3 to 75.0 ns by response rule 0 (hold, tick_delay 0), window [0.0, 95.0], margins 33.3 and 20.0 ns" "first_bit by the rule"
assert_contains "$out" "CSRW  0x41" "first bit written into the rule"
json="$(cat "$TMP/spi/spi_target.json")"
assert_contains "$json" '"RULE_CLK": "0x0000100B"' "pin-edge rule on CS fall with hold"
assert_contains "$json" '"RULE0_RESP_POS": "0x01000000"' "any position"
assert_contains "$json" '"RULE0_CTL": "0x00001400"' "drive, enabled"
st=0
tsec build specs/i2c_target.tse -o "$TMP/i2c" > /dev/null 2> "$TMP/err" || st=$?
err="$(cat "$TMP/err")"
assert_eq 2 "$st" "i2c exit status"
assert_contains "$err" "the wait that detects stop (abort) starts 12 cycles after stop may already be visible [R314]" "stop"
assert_contains "$err" "add a datasheet rule start -> stop >= 216.7 ns" "stop fix"
assert_eq 1 "$(grep -c ': error: ' "$TMP/err")" "i2c rejections"
[ ! -e "$TMP/i2c/i2c_target.img" ] || fail "i2c wrote an image"
pass
