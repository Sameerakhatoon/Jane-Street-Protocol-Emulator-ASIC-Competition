#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Optional FST tracing: a windowed waveform of one echoed byte.
. "$(dirname "$0")/lib.sh"

build_top echo
out="$(timeout 30 python3 tests/pty_client.py 61 41 -- \
    ./build/echo/tse_sim --uart --fst "$TMP/w.fst" --fst-from 0 --fst-cycles 20000 2>&1)" \
    || fail "$out"
[ -s "$TMP/w.fst" ] || fail "no FST written"
size="$(stat -c %s "$TMP/w.fst")"
[ "$size" -gt 200 ] || fail "FST too small: $size bytes"
pass "$size bytes"
