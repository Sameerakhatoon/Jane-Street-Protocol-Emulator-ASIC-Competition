#!/usr/bin/env bash
# The lexer gives line:col, kind and text per token; numbers, units and symbols split right.
. "$(dirname "$0")/lib.sh"
out="$(tsec lex specs/uart.tse)"
assert_contains "$out" "18:3 name param" "first token"
assert_contains "$out" "18:24 num 115200" "number"
assert_contains "$out" "18:31 name Hz" "unit after a number"
assert_contains "$out" "31:23 sym ->" "two-character symbol"
assert_eq eof "$(echo "$out" | tail -1 | cut -d' ' -f2)" "last token"
pass
