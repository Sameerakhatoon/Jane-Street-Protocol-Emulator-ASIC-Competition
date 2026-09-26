#!/usr/bin/env bash
# Every spec in specs/ passes the front end (parse, resolve, check) with zero errors.
. "$(dirname "$0")/lib.sh"
n=0
for f in specs/*.tse; do
    out="$(tsec check "$f" 2>&1)" || fail "$f: $out"
    assert_contains "$out" ": ok," "$f"
    n=$((n + 1))
done
[ "$n" -gt 0 ] || fail "no specs found"
echo "$test_name: $n specs check clean"
pass
