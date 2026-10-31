#!/bin/bash
# MCY test step: build the mutant (with the mutsel input for the miter),
# run <test>.sby on it and record PASS or FAIL. abc pdr sometimes cannot
# replay its counterexample into a trace (sby then says ERROR); the
# engine verdict is used in that case. A test with a timeout option may end
# in TIMEOUT (see below). Any other error stops the run.
exec 2>&1
set -ex
t=$1
if [ "$t" = test_eq ]; then ctrl=-c; else ctrl=; fi
bash $SCRIPTS/create_mutated.sh $ctrl -o mutated.il
for f in ../../*.sv ../../$t.sby; do ln -sf $f .; done
sby -f $t.sby || true
r=PASS
n=0
for st in ${t}*/status; do
    [ -f "$st" ] || continue
    d=$(dirname $st); n=$((n + 1))
    s=$(cut -d" " -f1 $st)
    if [ "$s" = PASS ]; then continue; fi
    # a timeout is no verdict: the miter counts the mutant as output-changing,
    # the kill test as not killed, so a timeout can only lower the score
    if [ "$s" = TIMEOUT ]; then
        if [ "$t" = test_eq ]; then r=FAIL; fi
        echo "timeout: $d" >> timeouts.txt
        continue
    fi
    if [ "$s" = FAIL ] || grep -q "returned FAIL" $d/logfile.txt; then r=FAIL; else exit 1; fi
done
[ $n -gt 0 ] || exit 1
echo "1 $r" >> output.txt
exit 0
