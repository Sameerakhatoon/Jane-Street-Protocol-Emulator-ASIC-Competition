#!/usr/bin/env bash
# CAN: the bit-level helpers pass their unit checks, then two bridge nodes
# share the wired-AND bus with the stand-in's CAN pins (TX uio7 released,
# RX ui6) through the harness: frames cross with stuffing and CRC-15, every
# frame is acknowledged in the ACK slot, and 0x0FF from B beats A when both
# start in the same bit; the loser sends again once the bus is idle.
. "$(dirname "$0")/lib.sh"

make -s --no-print-directory build/unit/unit_can >"$TMP/build" 2>&1 || fail "$(tail -20 "$TMP/build")"
u="$(./build/unit/unit_can)" || fail "$(grep FAIL <<<"$u")"

build_top echo
./build/echo/tse_sim --can "unix:$TMP/a" --can "unix:$TMP/b" --quantum 256 >"$TMP/out" 2>"$TMP/err" &
pid=$!
trap 'kill $pid 2>/dev/null || true; rm -rf "$TMP"' EXIT
out="$(timeout 30 python3 tests/can_client.py "$TMP/a" "$TMP/b" 2>&1)" || fail "$out $(cat "$TMP/err")"
kill $pid; wait $pid 2>/dev/null || true
err="$(cat "$TMP/err")"
assert_contains "$out" "A got 0FF#AB" "node A"
assert_contains "$out" "B got 200#0102030405060708 100#1122" "node B"
assert_contains "$err" "can A: lost arbitration" "arbitration"
assert_contains "$err" "can A: tx 2 rx 1 arbitration lost 1" "A stats"
assert_contains "$err" "stuff errors 0 crc errors 0" "checker"
c1="$(sed -n 's/^can A: rx 0FF .* at cycle \([0-9]*\)/\1/p' <<<"$err")"
c2="$(sed -n 's/^can B: rx 100 .* at cycle \([0-9]*\)/\1/p' <<<"$err")"
[ -n "$c1" ] && [ -n "$c2" ] && [ "$c1" -lt "$c2" ] || fail "0x0FF must finish before 0x100: $err"
pass "$(grep -c '^ok' <<<"$u") unit checks, 0x0FF at cycle $c1, 0x100 at $c2"
