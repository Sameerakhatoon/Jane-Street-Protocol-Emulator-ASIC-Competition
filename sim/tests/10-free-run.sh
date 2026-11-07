#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Time ownership: with no busy adapter the harness sleeps (no cycles);
# with --free-run it runs; --cycles ends the run on its own.
. "$(dirname "$0")/lib.sh"

build_top echo
./build/echo/tse_sim --uart >"$TMP/out" 2>"$TMP/err" &
pid=$!
sleep 1
kill $pid
wait $pid || true
idle="$(sed -n 's/.*top echo, \([0-9]*\) cycles.*/\1/p' "$TMP/err")"
[ -n "$idle" ] && [ "$idle" -lt 100000 ] || fail "idle harness ran time: $(cat "$TMP/err")"

err="$(timeout 20 ./build/echo/tse_sim --free-run --cycles 2000000 2>&1 >/dev/null)"
ran="$(sed -n 's/.*top echo, \([0-9]*\) cycles.*/\1/p' <<<"$err")"
[ -n "$ran" ] && [ "$ran" -ge 2000000 ] || fail "free run: $err"
pass "idle $idle cycles in 1 s, free run $ran"
