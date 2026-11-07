#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Check mode end to end: two hand-written monitor lanes on the Verilated
# chip watch an I2C bus driven by a stand-in controller, the host pops the
# event records over SPI, host/tse_check reports every Standard-mode rule,
# the I2C judge reads the same pins-only VCD, and every interval the chip
# measured is paired with the judge's.
#
#   examples/check/run.sh [clean|thd-sta|sweep] [OUT]   (default clean, build/check/CASE)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CASE="${1:-clean}"
OUT="${2:-$ROOT/build/check/$CASE}"
cd "$ROOT"
mkdir -p "$OUT"
export PYTHONDONTWRITEBYTECODE=1

step() { printf "== %s\n" "$*"; }
die() { echo "FAIL check-$CASE: $*"; exit 1; }

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "assemble the monitor programs"
for p in i2c_edges i2c_cond; do
    python3 -m tools.tseasm "$HERE/$p.s" -o "$OUT/$p.hex" -l "$OUT/$p.lst" || die "assembler"
done

step "load, run, drive the bus, pop the event log"
VCD="$OUT/check.vcd"
python3 "$HERE/check_demo.py" --sim "$SIM --pin-server --vcd $VCD" --out "$OUT" "$CASE" \
    >"$OUT/demo.log" 2>&1 || { grep -v "^tse_sim:" "$OUT/demo.log"; die "demo"; }
grep -v "^tse_sim:" "$OUT/demo.log"
FROM="$(sed -n "s/^window: \([0-9.]*\) s$/\1/p" "$OUT/demo.log")"
[ -n "$FROM" ] || die "no run window in the demo log"

step "host/tse_check report"
rc=0
python3 host/tse_check report "$HERE/i2c_sm.json" "$OUT/records.txt" >"$OUT/report.txt" || rc=$?
cat "$OUT/report.txt"
[ "$rc" -le 1 ] || die "report tool"

step "I2C judge on SCL (uio_in2) and SDA (uio_in3), Standard mode, from $FROM s"
jrc=0
python3 -m verif.judges i2c "$VCD" --map scl=uio_in2 --map sda=uio_in3 --speed sm --require-ack \
    --from "$FROM" --expect "$OUT/expect.txt" >"$OUT/judge.log" || jrc=$?
grep -v "^ " "$OUT/judge.log" | grep -v "^VIOLATION" || true
grep "^VIOLATION" "$OUT/judge.log" | head -4 || true
grep -q "frames differ" "$OUT/judge.log" && die "the judge decoded other transactions"

step "chip intervals against the judge's"
python3 "$HERE/crosscheck.py" "$HERE/i2c_sm.json" "$OUT/records.txt" "$VCD" --from "$FROM" \
    >"$OUT/crosscheck.txt" || { cat "$OUT/crosscheck.txt"; die "crosscheck"; }
cat "$OUT/crosscheck.txt"

nviol="$(grep -c "^t=.*VIOLATION" "$OUT/report.txt" || true)"
nposs="$(grep -c "^t=.*possible" "$OUT/report.txt" || true)"
njudge="$(grep -c "^VIOLATION" "$OUT/judge.log" || true)"
case "$CASE" in
clean)
    [ "$rc" -eq 0 ] && [ "$jrc" -eq 0 ] || die "a clean bus must pass both"
    echo "PASS check-clean: 0 violations on the chip and in the judge" ;;
thd-sta)
    other="$(grep "^t=" "$OUT/report.txt" | grep -v " tHD;STA " || true)"
    jother="$(grep "^VIOLATION" "$OUT/judge.log" | grep -v " tHD;STA: " || true)"
    [ -z "$other$jother" ] || die "violations other than tHD;STA"
    [ "$nviol" -gt 0 ] && [ "$nviol" -eq "$njudge" ] || die "chip $nviol, judge $njudge tHD;STA violations"
    echo "PASS check-thd-sta: $nviol tHD;STA violations flagged by the chip and by the judge, nothing else" ;;
sweep)
    grep -q "^first failing offset: 5 t" "$OUT/demo.log" || die "first failing step"
    grep -q "^failing edge: tHD;STA" "$OUT/demo.log" || die "failing edge"
    echo "PASS check-sweep: last pass $(sed -n 's/^last passing offset: //p' "$OUT/demo.log"), first fail at 240 cycles (possible), $nviol certain and $nposs possible, judge flags $njudge" ;;
esac
