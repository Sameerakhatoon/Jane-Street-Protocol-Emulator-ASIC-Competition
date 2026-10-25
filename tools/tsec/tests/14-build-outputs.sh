#!/usr/bin/env bash
# -o writes the image, listing and report; --emit all adds UPPAAL XML and the proof harness.
. "$(dirname "$0")/lib.sh"
tsec build specs/uart.tse -o "$TMP/uart" --emit all > /dev/null || fail "build"
for f in uart.img uart.lst uart.report.txt uart_tx_side.xml uart_rx_side.xml \
         proof/top_rx_side.sv proof/rx_side.sby proof/rx_side.hex proof/tse_window_check.sv; do
    [ -s "$TMP/uart/$f" ] || fail "missing $f"
done
assert_eq "@000" "$(sed -n 3p "$TMP/uart/uart.img")" "lane 0 address"
assert_contains "$(cat "$TMP/uart/uart.img")" "@100" "lane 1 at instruction 256"
assert_eq 32 "$(grep -c '^[0-9a-f]\{4\}$' "$TMP/uart/uart.img")" "image words"
"$PY" -c "import sys, xml.etree.ElementTree as E; [E.parse(p) for p in sys.argv[1:]]" \
    "$TMP"/uart/*.xml || fail "UPPAAL XML is not well formed"
assert_contains "$(cat "$TMP/uart/proof/top_rx_side.sv")" ".LO(210), .HI(311)" "window bounds"
pass
