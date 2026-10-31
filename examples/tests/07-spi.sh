#!/usr/bin/env bash
# SPI mode 0 target and controller lanes, each judged on the pins.
. "$(dirname "$0")/lib.sh"

for role in target controller; do
    bash examples/spi/run.sh $role "$OUT/$role" >"$OUT/$role.log" 2>&1 \
        || { tail -30 "$OUT/$role.log" >&2; fail "$role, see $OUT/$role.log"; }
done
pass "$(tail -1 "$OUT/target.log"); $(tail -1 "$OUT/controller.log")"
