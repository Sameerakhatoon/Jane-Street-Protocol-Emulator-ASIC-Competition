#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# parse(print(parse(s))) == parse(s) for every spec and positive test (spans aside),
# and printing is stable: fmt(fmt(s)) == fmt(s).
. "$(dirname "$0")/lib.sh"
for f in specs/*.tse $TESTS/pos/*.tse; do
    tsec fmt "$f" > "$TMP/a.tse" || fail "fmt $f"
    tsec fmt "$TMP/a.tse" > "$TMP/b.tse" || fail "the printed $f does not parse"
    cmp -s "$TMP/a.tse" "$TMP/b.tse" || fail "printing is not stable for $f"
    "$PY" -c '
import sys
from tools.tsec.parser import parse
a, b = (parse(open(p).read()) for p in sys.argv[1:])
sys.exit(0 if a == b else 1)
' "$f" "$TMP/a.tse" || fail "printing changed the tree of $f"
done
pass
