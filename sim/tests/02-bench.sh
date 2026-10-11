#!/usr/bin/env bash
# Build the stand-in top and measure raw harness speed (--bench).
# The UART demo needs well under 1 M cycles/s; the bar here only
# catches a harness that got accidentally slow.
. "$(dirname "$0")/lib.sh"

build_top echo
out="$(./build/echo/tse_sim --bench 3000000)"
assert_contains "$out" "bench: top echo, 3000000 cycles" "bench line"
cps="$(sed -n 's/.* = \([0-9]*\) cycles\/s.*/\1/p' <<<"$out")"
[ "${cps:-0}" -gt 1000000 ] || fail "too slow: $out"
pass "$cps cycles/s"
