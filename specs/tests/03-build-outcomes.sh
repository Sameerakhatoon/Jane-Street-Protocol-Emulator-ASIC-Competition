#!/usr/bin/env bash
# Build every row of expected-outcomes.txt and compare the exit status, the first diagnostic's
# code and its reason. When the compiler starts lowering a construct, or rejects for a new
# reason, the row no longer matches and this test fails, so the change is seen and the file
# is updated on purpose. Every spec needs at least one row.
# Rows in expected-outcomes-slow.txt take many minutes each; they run with SPECS_SLOW=1.
# SPECS_BLESS=1 rewrites the outcome columns from the current compiler (review the diff).
. "$(dirname "$0")/lib.sh"
FAST=$TESTS/expected-outcomes.txt
SLOW=$TESTS/expected-outcomes-slow.txt
files=("$FAST")
[ -n "${SPECS_SLOW:-}" ] && files+=("$SLOW")

outcome() {                             # outcome SPEC ARGS -> "exit|code|reason"
    local spec="$1" args="$2" st=0 first code reason
    [ "$args" = "-" ] && args=""
    rm -rf "$TMP/out"
    # shellcheck disable=SC2086
    tsec build "specs/$spec" $args -o "$TMP/out" > /dev/null 2> "$TMP/err" || st=$?
    first="$(grep -m1 ': error: ' "$TMP/err" || true)"
    code="$(printf '%s' "$first" | grep -oE '\[[A-Z][0-9]+\]$' | tr -d '[]' || true)"
    reason="$(printf '%s' "$first" | sed -e 's/^.*: error: //' -e 's/ \[[A-Z][0-9]*\]$//')"
    if [ "$st" -eq 0 ] && [ ! -s "$TMP/out/${spec%.tse}.img" ]; then
        reason="accepted but no image written"
    fi
    echo "$st|${code:--}|${reason:--}"
}

if [ -n "${SPECS_BLESS:-}" ]; then
    for expect in "${files[@]}"; do
        while IFS= read -r line; do
            case "$line" in '#'*|'') echo "$line"; continue ;; esac
            IFS='|' read -r spec args _ <<< "$line"
            echo "$spec|$args|$(outcome "$spec" "$args")"
        done < "$expect" > "$TMP/new"
        cp "$TMP/new" "$expect"
    done
fi

rows=0
for expect in "${files[@]}"; do
    while IFS='|' read -r spec args want_st want_code want_reason; do
        case "$spec" in '#'*|'') continue ;; esac
        got="$(outcome "$spec" "$args")"
        [ "$got" = "$want_st|$want_code|$want_reason" ] \
            || fail "$spec $args: expected '$want_st|$want_code|$want_reason' got '$got'"
        rows=$((rows + 1))
    done < "$expect"
done

for f in specs/*.tse; do
    grep -q "^$(basename "$f")|" "$FAST" "$SLOW" || fail "$f has no row in the expected outcomes"
done
echo "$test_name: $rows build outcomes as expected"
pass
