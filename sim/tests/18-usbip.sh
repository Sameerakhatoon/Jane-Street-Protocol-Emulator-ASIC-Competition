#!/usr/bin/env bash
# USB/IP framing skeleton: device list, import, URB submit and unlink replays.
. "$(dirname "$0")/lib.sh"

make -s --no-print-directory build/unit/unit_usbip >"$TMP/build" 2>&1 || fail "$(tail -20 "$TMP/build")"
out="$(./build/unit/unit_usbip)" || fail "$(grep FAIL <<<"$out")"
pass "$(grep -c '^ok' <<<"$out") checks"
