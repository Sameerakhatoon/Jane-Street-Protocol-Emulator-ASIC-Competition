#!/usr/bin/env bash
# CAN receive with the ACK in the slot at 500 and 125 kbit/s, judged on the bus;
# 250 kbit/s is built with its timing proof.
. "$(dirname "$0")/lib.sh"

for r in 500e3 125e3; do
    BITRATE=$r bash examples/can/run.sh "$OUT/$r" >"$OUT/$r.log" 2>&1 \
        || { tail -30 "$OUT/$r.log" >&2; fail "$r bit/s, see $OUT/$r.log"; }
done
python3 -m tools.tsec build specs/can_rx_ack.tse -D bitrate=250e3 --fclk 60e6 -o "$OUT/250e3" \
    >"$OUT/250e3.log" 2>&1 || fail "250 kbit/s build, see $OUT/250e3.log"
pass "$(tail -1 "$OUT/500e3.log" | sed 's/^PASS e2e-can: //'); 125k: $(tail -1 "$OUT/125e3.log" | sed 's/^PASS e2e-can: //')"
