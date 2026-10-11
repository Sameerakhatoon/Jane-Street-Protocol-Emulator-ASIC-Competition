#!/usr/bin/env bash
# UART pty bridge, both directions, on the stand-in top:
# pty -> 8N1 on ui_in[0] -> chip upper-cases -> 8N1 on uo_out[0] -> pty.
. "$(dirname "$0")/lib.sh"

build_top echo
send="$(printf 'hello from linux\n' | od -An -tx1 | tr -d ' \n')"
want="$(printf 'HELLO FROM LINUX\n' | od -An -tx1 | tr -d ' \n')"
out="$(timeout 30 python3 tests/pty_client.py "$send" "$want" -- ./build/echo/tse_sim --uart 2>&1)" \
    || fail "$out"
assert_contains "$out" "got b'HELLO FROM LINUX\n'" "echo"
pass
