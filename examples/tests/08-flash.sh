#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# SPI flash lane read over serprog: stand-in controller, then a controller
# lane; JEDEC ID, 16-byte read and status register, judged on the pins.
. "$(dirname "$0")/lib.sh"

for mode in stand-in lanes; do
    args=()
    [ $mode = lanes ] && args=(--lanes)
    bash examples/flash/run.sh "${args[@]}" "$OUT/$mode" >"$OUT/$mode.log" 2>&1 \
        || { tail -30 "$OUT/$mode.log" >&2; fail "$mode, see $OUT/$mode.log"; }
done
pass "$(tail -1 "$OUT/stand-in.log" | cut -d: -f1); $(tail -1 "$OUT/lanes.log" | cut -d: -f1)"
