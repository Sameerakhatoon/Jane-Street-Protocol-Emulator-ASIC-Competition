#!/usr/bin/env bash
# Every spec in specs/ passes the front end with zero errors.
. "$(dirname "$0")/lib.sh"
for f in specs/*.tse; do
    out="$(tsec check "$f" 2>&1)" || fail "$f: $out"
    assert_contains "$out" ": ok," "$f"
done
pass
