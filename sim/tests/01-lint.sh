#!/usr/bin/env bash
# The stand-in top is our RTL: it must be Verilator -Wall clean.
. "$(dirname "$0")/lib.sh"

out="$(make -s lint 2>&1)" || fail "verilator lint: $out"
pass
