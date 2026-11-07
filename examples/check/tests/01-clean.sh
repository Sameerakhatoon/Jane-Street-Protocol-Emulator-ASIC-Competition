#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Compliant Standard-mode traffic: every edge reported with one latency, no
# violation on the chip or in the I2C judge, every interval paired.
. "$(dirname "$0")/lib.sh"

demo bash examples/check/run.sh clean "$OUT"
grep -q "^PASS check-clean" "$OUT/run.log" || fail "no PASS line"
grep -q "^PASS crosscheck" "$OUT/run.log" || fail "crosscheck"
pass "$(grep "intervals paired" "$OUT/run.log")"
