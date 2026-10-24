#!/usr/bin/env bash
# Every spec has a judge case, every case's expect file and options agree with its spec
# (checked on traces written from the expected frames), and the manifest runs: a case
# without its demo trace is an allowed skip.
. "$(dirname "$0")/lib.sh"
J=specs/judge
for f in specs/*.tse; do
    n="$(basename "${f%.tse}")"
    grep -qE "^$n[[:space:]]" $J/manifest.txt || fail "$f has no judge case in $J/manifest.txt"
done
out="$("$PY" $J/selfcheck.py)" || fail "$out"
echo "$out"
out="$("$PY" -m verif.judges.run_judges $J/manifest.txt)" || fail "$out"
assert_contains "$out" " 0 failed" "manifest run"
pass
