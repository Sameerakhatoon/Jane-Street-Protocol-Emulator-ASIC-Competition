#!/usr/bin/env bash
# Run every numbered tsec test and report pass and fail totals.
set -uo pipefail

cd "$(dirname "$0")"

passed=0
failed=0
fails=()

for t in [0-9]*.sh; do
    if bash "$t"; then
        passed=$((passed + 1))
    else
        failed=$((failed + 1))
        fails+=("${t%.sh}")
    fi
done

echo
echo "passed: $passed   failed: $failed"
if [ "$failed" -gt 0 ]; then
    echo "failed tests:"
    for f in "${fails[@]}"; do echo "  $f"; done
    exit 1
fi
