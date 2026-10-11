#!/usr/bin/env bash
# OpenOCD remote_bitbang, SWD: line reset, JTAG-to-SWD, read DPIDR, write
# SELECT and CTRL/STAT, read them back, against the stand-in SW-DP on
# SWCLK ui5 and SWDIO uio2 (an open-drain WiredAnd bus with the chip).
# The turnarounds must never have both sides driving.
. "$(dirname "$0")/lib.sh"

build_top echo
./build/echo/tse_sim --rbb 0 >"$TMP/out" 2>"$TMP/err" &
pid=$!
trap 'kill $pid 2>/dev/null || true; rm -rf "$TMP"' EXIT
for _ in $(seq 50); do
    grep -q '^rbb ' "$TMP/out" && break
    sleep 0.1
done
port="$(sed -n 's/^rbb 127.0.0.1:\([0-9]*\)/\1/p' "$TMP/out")"
[ -n "$port" ] || fail "no rbb port: $(cat "$TMP/err")"
out="$(timeout 30 python3 tests/rbb_replay.py "$port" swd 2>&1)" || fail "$out"
assert_contains "$out" "DPIDR 0x0BC11477" "dpidr"
kill $pid; wait $pid 2>/dev/null || true
assert_contains "$(cat "$TMP/err")" " 0 SWDIO clashes" "clashes"
pass "$(sed -n 's/^DPIDR/DPIDR/p' <<<"$out")"
