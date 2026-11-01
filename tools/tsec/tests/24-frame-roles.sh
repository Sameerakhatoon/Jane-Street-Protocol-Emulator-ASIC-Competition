#!/usr/bin/env bash
# Frame roles on the frame engine: the 10BASE-T transmitter streamed from the host and
# buffered in the data window, their proof harnesses, and the frame specs the lane cannot
# take, each with its reason.
. "$(dirname "$0")/lib.sh"
st=0
out="$(tsec build specs/eth10_tx.tse --emit all -o "$TMP/tx")" || st=$?
assert_eq 0 "$st" "eth10_tx exit status"
assert_contains "$out" "first TXDATA write after a half-bit strobe: 1 to 1 cycles (needs [0, 1] cycles)  ok"
assert_contains "$out" "TXDATA write after TXNEED: 1 to 1 cycles (needs [0, 4] cycles)  ok"
assert_contains "$out" "hold: 576 to 576 cycles (needs >= 576.00 ticks)  ok"
assert_contains "$out" "assumption host_ready" "host_ready labelled"
top="$(cat "$TMP/tx/proof/top_mac_tx.sv")"
assert_contains "$top" "assume (h_tx_we)" "host_ready in the harness"
assert_eq 5 "$(grep -c 'tse_window_check #' "$TMP/tx/proof/top_mac_tx.sv")" "window checkers"

st=0
out="$(tsec build $TESTS/targets/eth10_tx_window.tse --emit all -o "$TMP/win")" || st=$?
assert_eq 0 "$st" "eth10_tx_window exit status"
assert_contains "$out" "LDW   X" "words read from the data window"
assert_contains "$out" "host contract, data window buf: the words of 'dst' to 'payload' in reverse"
json="$(cat "$TMP/win/eth10_tx_window.json")"
assert_contains "$json" '"data_window": "words 0 to 31"' "window in the image JSON"
if grep -q 'assume (h_tx_we)' "$TMP/win/proof/top_mac_tx.sv"; then fail "no host_ready, no assumption"; fi

for row in "eth10_rx R314 start-of-frame detection armed again" \
           "usb_ls_device R307 pin plan impossible: assist 'line' codes the line on 2 wires"; do
    spec="${row%% *}"
    rest="${row#* }"
    code="${rest%% *}"
    st=0
    tsec build "specs/$spec.tse" > /dev/null 2> "$TMP/$spec.err" || st=$?
    assert_eq 2 "$st" "$spec exit status"
    first="$(grep -m1 -E '\[R[0-9]+\]$' "$TMP/$spec.err")"
    assert_contains "$first" "[$code]" "$spec first reason"
    assert_contains "$first" "${rest#* }" "$spec message"
done
pass
