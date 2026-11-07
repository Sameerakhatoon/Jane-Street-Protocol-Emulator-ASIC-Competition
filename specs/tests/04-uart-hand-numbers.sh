#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# The UART roles build, and the compiler's report agrees with the hand-computed numbers
# in the comments of uart_tx.tse and uart_rx.tse at both clocks and both rates.
. "$(dirname "$0")/lib.sh"

report() { tsec build "specs/$1" "${@:2}" -o "$TMP/out"; }

r="$(report uart_tx.tse)"
assert_contains "$r" "error  3.50 <= 10.18" "tx 115200 at 60 MHz"
assert_contains "$r" "(needs 510.4)" "tx hold at 60 MHz"
r="$(report uart_tx.tse --fclk 50e6)"
assert_contains "$r" "<=  8.49" "tx stop budget at 50 MHz"
assert_contains "$r" "(needs 425.3)" "tx hold at 50 MHz"
r="$(report uart_tx.tse -D baud=1000000)"
assert_contains "$r" "error  0.00 <=  1.17" "tx 1 Mbaud at 60 MHz"
r="$(report uart_tx.tse -D baud=1000000 --fclk 50e6)"
assert_contains "$r" "error  0.00 <=  0.98" "tx 1 Mbaud at 50 MHz"

r="$(report uart_rx.tse)"
assert_contains "$r" "window [208.3, 312.5]" "rx start window at 60 MHz"
assert_contains "$r" "(limit 250)" "rx re-arm at 60 MHz"
r="$(report uart_rx.tse --fclk 50e6)"
assert_contains "$r" "window [173.6, 260.4]" "rx start window at 50 MHz"
assert_contains "$r" "(limit 208)" "rx re-arm at 50 MHz"
r="$(report uart_rx.tse -D baud=1000000)"
assert_contains "$r" "window [24.0, 36.0]" "rx 1 Mbaud window at 60 MHz"
assert_contains "$r" "(limit 28)" "rx 1 Mbaud re-arm at 60 MHz"
r="$(report uart_rx.tse -D baud=1000000 --fclk 50e6)"
assert_contains "$r" "window [20.0, 30.0]" "rx 1 Mbaud window at 50 MHz"
assert_contains "$r" "(limit 24)" "rx 1 Mbaud re-arm at 50 MHz"
pass
