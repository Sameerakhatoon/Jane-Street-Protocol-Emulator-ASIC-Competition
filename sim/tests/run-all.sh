#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Walk every numbered test in lexical order. Exit 77 from a test means
# skipped (a missing prerequisite such as user namespaces), not failed.
set -u
cd "$(dirname "$0")/.."

passed=0
failed=0
skipped=0
failed_list=()
for t in tests/[0-9]*.sh; do
    bash "$t"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        passed=$((passed + 1))
    elif [ "$rc" -eq 77 ]; then
        skipped=$((skipped + 1))
    else
        failed=$((failed + 1))
        failed_list+=("$(basename "${t%.*}")")
    fi
done

echo
echo "sim: passed: $passed   skipped: $skipped   failed: $failed"
if [ "$failed" -ne 0 ]; then
    echo "failed tests:"
    for t in "${failed_list[@]}"; do
        echo "  $t"
    done
    exit 1
fi
exit 0
