#!/usr/bin/env bash
# Loader path end to end: the host CLI drives the Verilated full chip through
# --pin-server. It loads a UART image over the byte bus (BEGIN, BIST, WRB,
# COMMIT with CRC-32, readback verify), peeks the program words back and
# reads STATUS.
. "$(dirname "$0")/lib.sh"

dir="$(make -s --no-print-directory print-RTL_DIR)"
[ -f "${dir}/tse_chip.sv" ] || skip "no chip RTL at '$dir'"
[ -f ../host/tse_cli.py ] || skip "no host CLI in ../host"
build_top chip
img="$TMP/u.tsei"
python3 ../host/tse_cli.py image build -o "$img" --lane 0=../tools/tseasm/tests/uart_tx.s >/dev/null \
    || fail "image build"
sim="$SIM_ROOT/build/chip/tse_sim --pin-server"
out="$(timeout 120 python3 ../host/tse_cli.py --target "pipe:$sim" \
        load "$img" + peek 0 2 + status 2>&1)" || fail "$out"
assert_contains "$out" "0x0000: 0x41FD4011" "word 0"
assert_contains "$out" "0x0001: 0x4207C300" "word 1"
assert_contains "$out" "bist_pass=1" "self-test"
assert_contains "$out" "loaded=1" "commit"

st="$(timeout 60 python3 ../host/tse_cli.py --target "pipe:$sim" status 2>&1)" || fail "$st"
assert_contains "$st" "mode run, STATUS=" "status on a fresh chip"
pass "$(grep -o 'crc 0x[0-9A-F]*' <<<"$out")"
