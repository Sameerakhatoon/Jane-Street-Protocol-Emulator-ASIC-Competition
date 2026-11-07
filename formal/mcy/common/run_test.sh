#!/bin/bash
# SPDX-License-Identifier: Apache-2.0
# MCY test step: build the mutant (with the mutsel input for the miter),
# run <test>.sby on it and record PASS or FAIL. abc pdr sometimes cannot
# replay its counterexample into a trace (sby then says ERROR); the
# engine verdict is used in that case. A test with a timeout option may end
# in TIMEOUT (see below). A mutant on a flop's clock pin leaves a derived
# clock, or one clock used at both edges (an inverted pin), which prep takes
# only with multiclock; that test runs again with it. Any other error stops
# the run.
# sby itself can hang after a task times out (it waits on the engine it
# killed), so each sby run is cut off after MCY_SBY_LIMIT seconds; a task
# with no verdict then counts as a timeout. The verdict is read from the
# task's status file or, failing that, the DONE line of its log.
exec 2>&1
set -ex
t=$1
lim=${MCY_SBY_LIMIT:-1200}
if [ "$t" = test_eq ]; then ctrl=-c; else ctrl=; fi
bash $SCRIPTS/create_mutated.sh $ctrl -o mutated.il
for f in ../../*.sv ../../$t.sby; do ln -sf $f .; done
timeout -k 10 $lim sby -f $t.sby || true
if grep -qsE "derived clocks are only supported|used with opposite polarity" ${t}*/logfile.txt; then
    rm -rf ${t} ${t}_* $t.sby
    sed '/^\[options\]/a multiclock on' ../../$t.sby > $t.sby
    timeout -k 10 $lim sby -f $t.sby || true
fi
r=PASS
n=0
for d in ${t}*/; do
    d=${d%/}
    [ -f "$d/logfile.txt" ] || continue
    n=$((n + 1))
    s=$(cut -d" " -f1 $d/status 2>/dev/null || true)
    [ -n "$s" ] || s=$(sed -n 's/.*DONE (\([A-Z]*\),.*/\1/p' $d/logfile.txt | tail -1)
    [ -n "$s" ] || s=TIMEOUT
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
