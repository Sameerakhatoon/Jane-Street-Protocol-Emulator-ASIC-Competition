#!/usr/bin/env bash
# Pins-only VCD: decode both UART directions from the file alone, the
# way sigrok's uart decoder will, and check the bytes.
. "$(dirname "$0")/lib.sh"

build_top echo
out="$(timeout 30 python3 tests/pty_client.py 61 41 -- \
    ./build/echo/tse_sim --uart --vcd "$TMP/p.vcd" 2>&1)" || fail "$out"
got="$(python3 tests/vcd_uart.py "$TMP/p.vcd" ui_in0 115200),$(python3 tests/vcd_uart.py "$TMP/p.vcd" uo_out0 115200)"
assert_eq "61,41" "$got" "bytes decoded from the VCD (rx,tx)"
pass
