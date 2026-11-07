#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Walk every numbered Check and Trigger test in lexical order.
set -u
cd "$(dirname "$0")"

passed=0
failed=0
failed_list=()
for t in [0-9]*.sh; do
    if bash "$t"; then
        passed=$((passed + 1))
    else
        failed=$((failed + 1))
        failed_list+=("${t%.*}")
    fi
done

echo
echo "check: passed: $passed   failed: $failed"
if [ "$failed" -ne 0 ]; then
    printf "  %s\n" "${failed_list[@]}"
    exit 1
fi
exit 0
