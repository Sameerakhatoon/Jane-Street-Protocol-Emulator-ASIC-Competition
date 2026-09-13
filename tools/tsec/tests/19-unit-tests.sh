#!/usr/bin/env bash
# The per-stage unit tests (pytest, with Hypothesis fuzzing) pass.
. "$(dirname "$0")/lib.sh"
out="$(HYPOTHESIS_STORAGE_DIRECTORY="$TMP/hyp" "$PY" -m pytest -q -p no:cacheprovider $TESTS/unit 2>&1)" \
    || fail "$(echo "$out" | tail -30)"
echo "$out" | tail -1
pass
