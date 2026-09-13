#!/usr/bin/env bash
# `tsec parse` prints the syntax tree with spans; golden.
. "$(dirname "$0")/lib.sh"
golden parse-uart tsec parse specs/uart.tse
assert_eq 0 "$status" "exit status"
pass
