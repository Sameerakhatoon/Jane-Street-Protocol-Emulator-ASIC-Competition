#!/usr/bin/env bash
# With the bounds the rejections name, both targets build: SPI at 1.875 MHz with t_css 170 ns,
# and I2C with a start-to-stop assumption. Reports are golden; images and JSON are written.
. "$(dirname "$0")/lib.sh"
golden spi-target-relaxed tsec build specs/spi_target.tse -D t_css=170e-9 -D f_sck=1.875e6 -o "$TMP/spi"
assert_eq 0 "$status" "spi exit status"
[ -s "$TMP/spi/spi_target.img" ] && [ -s "$TMP/spi/spi_target.json" ] || fail "spi outputs"
golden i2c-target-bounded tsec build $TESTS/targets/i2c_target_bounded.tse -o "$TMP/i2c" --emit all
assert_eq 0 "$status" "i2c exit status"
for f in i2c_target_bounded.img i2c_target_bounded.json i2c_target_bounded.lir; do
    [ -s "$TMP/i2c/$f" ] || fail "missing $f"
done
assert_contains "$(cat "$TMP/err" 2>/dev/null; cat "$TMP/i2c-target-bounded.err")" "[W260]" "hold warning"
json="$(cat "$TMP/i2c/i2c_target_bounded.json")"
assert_contains "$json" '"LANE_CFG": "0x' "lane config"
assert_contains "$json" '"PAD_OUT": "0x000000E1"' "open-drain pad, idle released"
assert_contains "$json" "\"LANE_CFG\": \"0x08400080\"" "jmp_pin scl, alternate: sda changed"
pass
