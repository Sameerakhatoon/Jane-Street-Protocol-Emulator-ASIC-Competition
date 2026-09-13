#!/usr/bin/env bash
# The SPI and I2C target specs check clean, but build rejects them with R300 (rule R1, no image).
. "$(dirname "$0")/lib.sh"
for s in spi_target i2c_target; do
    st=0
    tsec build specs/$s.tse -o "$TMP/$s" > /dev/null 2> "$TMP/err" || st=$?
    assert_eq 2 "$st" "$s exit status"
    assert_contains "$(cat "$TMP/err")" "[R300]" "$s"
    [ ! -e "$TMP/$s/$s.img" ] || fail "$s wrote an image"
done
pass
