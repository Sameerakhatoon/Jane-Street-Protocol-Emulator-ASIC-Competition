#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# UART pty bridge on the real UART test RTL (RTL_DIR): bytes from the pty
# go onto the ui_in byte bus with a uio_in[0] strobe, the chip's own
# transmitter sends them on uo_out[0], the adapter decodes them back.
# Includes 00, ff and 55 so every bit pattern edge case crosses.
. "$(dirname "$0")/lib.sh"

rtl="$(make -s --no-print-directory print-RTL_DIR)"
[ -f "${rtl}/tt_um_tse_sk.sv" ] || skip "no UART test RTL at '$rtl'"
build_top uart
send="$(printf 'TSE UART\n' | od -An -tx1 | tr -d ' \n')00ff55"
out="$(timeout 30 python3 tests/pty_client.py "$send" "$send" -- ./build/uart/tse_sim --uart 2>&1)" \
    || fail "$out"
pass
