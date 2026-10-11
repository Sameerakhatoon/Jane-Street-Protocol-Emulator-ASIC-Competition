#!/usr/bin/env bash
# OpenOCD remote_bitbang, JTAG: replay the driver's byte stream for a TAP
# reset, an IDCODE scan, an IR scan of BYPASS and a bypass data scan against
# the stand-in TAP on ui2..ui4 / uo3, in lockstep over TCP.
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
out="$(timeout 30 python3 tests/rbb_replay.py "$port" jtag 2>&1)" || fail "$out"
assert_contains "$out" "IDCODE 0x15E00001" "idcode"
kill $pid; wait $pid 2>/dev/null || true
stats="$(grep 'rbb ' "$TMP/err" || true)"
pass "$(sed -n 's/^IDCODE \(0x[0-9A-F]*\).*/IDCODE \1/p' <<<"$out"), ${stats#tse_sim: }"
