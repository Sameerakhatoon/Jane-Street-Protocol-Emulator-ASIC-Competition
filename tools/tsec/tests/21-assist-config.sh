#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Assist declarations become register values in the image JSON, packed by the register table:
# CRC-16/USB, a DPLL at 115,200 baud, and the I2C ACK rule in pin-edge mode.
. "$(dirname "$0")/lib.sh"
tsec build $TESTS/targets/uart_rx_assists.tse -o "$TMP/a" > /dev/null || fail "build"
json="$(cat "$TMP/a/uart_rx_assists.json")"
for want in '"CRC_POLY": "0x80050000"' '"CRC_INIT": "0xFFFF0000"' '"CRC_XOR": "0xFFFF0000"' \
            '"CRC_CHECK": "0x800D0000"' '"CRC_CTL": "0x0000006F"' '"crc16.catalogue": "CRC-16/USB"' \
            '"crc16.residue": "0xB001"' '"DPLL_PERIOD": "0x0000208D"' '"DPLL_SPT": "0x00001047"' \
            '"DPLL_SJW": "0x000003C0"' '"DPLL_CTL": "0x0000000B"' '"RULE0_MATCH": "0x00FE00A0"' \
            '"RULE0_RESP_POS": "0x00070000"' '"RULE0_CTL": "0x00001400"' '"RULE_CLK": "0x0000012B"'; do
    assert_contains "$json" "$want" "json"
done
pass
