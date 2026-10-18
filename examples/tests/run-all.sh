#!/usr/bin/env bash
# Walk every numbered example test in lexical order. Exit 77 means skipped.
set -u
cd "$(dirname "$0")"

passed=0
failed=0
skipped=0
failed_list=()
for t in [0-9]*.sh; do
    bash "$t"
    rc=$?
    if [ "$rc" -eq 0 ]; then
        passed=$((passed + 1))
    elif [ "$rc" -eq 77 ]; then
        skipped=$((skipped + 1))
    else
        failed=$((failed + 1))
        failed_list+=("${t%.*}")
    fi
done

echo
echo "examples: passed: $passed   skipped: $skipped   failed: $failed"
if [ "$failed" -ne 0 ]; then
    printf "  %s\n" "${failed_list[@]}"
    exit 1
fi
exit 0
