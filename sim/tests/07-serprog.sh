#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# serprog framing: replay flashrom's init sequence, a JEDEC ID and a read
# against the behavioural flash, with model time advancing per job.
. "$(dirname "$0")/lib.sh"

build_top echo
./build/echo/tse_sim --serprog 0 >"$TMP/out" 2>"$TMP/err" &
pid=$!
trap 'kill $pid 2>/dev/null; rm -rf "$TMP"' EXIT
for _ in $(seq 50); do
    grep -q '^serprog ' "$TMP/out" && break
    sleep 0.1
done
port="$(sed -n 's/^serprog 127.0.0.1:\([0-9]*\)/\1/p' "$TMP/out")"
[ -n "$port" ] || fail "no serprog port: $(cat "$TMP/err")"
out="$(timeout 20 python3 tests/serprog_replay.py "$port" 2>&1)" || fail "$out"
assert_contains "$out" "JEDEC ID: ef4018" "jedec"
pass
