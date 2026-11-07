#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# OpenOCD remote_bitbang, SWD: line reset, JTAG-to-SWD, read DPIDR, write
# SELECT and CTRL/STAT, read them back, against the stand-in SW-DP on
# SWCLK ui5 and SWDIO uio2 (an open-drain WiredAnd bus with the chip).
# The turnarounds must never have both sides driving, neither by the clash
# count nor in the VCD's resolved swdio wire and its two driver wires.
. "$(dirname "$0")/lib.sh"

build_top echo
./build/echo/tse_sim --rbb 0 --vcd "$TMP/swd.vcd" >"$TMP/out" 2>"$TMP/err" &
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
vcd="$(python3 - "$TMP/swd.vcd" <<'EOF'
import sys
ids, val, both, chip, low = {}, {}, 0, 0, 0
for ln in open(sys.argv[1]):
    w = ln.split()
    if w[:1] == ["$var"]:
        ids[w[3]] = w[4]
    elif ln[0] == "#":
        both |= val.get("swdio_host", 0) and val.get("swdio_chip", 0)
    elif ln[0] in "01" and ids.get(ln[1:].strip(), "").startswith("swdio"):
        val[ids[ln[1:].strip()]] = int(ln[0])
        chip |= val.get("swdio_chip", 0)
        low |= val.get("swdio", 1) == 0
print("wires=%d both=%d chip=%d low=%d" % (len([n for n in ids.values() if n.startswith("swdio")]), both, chip, low))
EOF
)"
assert_eq "wires=3 both=0 chip=1 low=1" "$vcd" "resolved swdio in the VCD"
pass "$(sed -n 's/^DPIDR/DPIDR/p' <<<"$out")"
