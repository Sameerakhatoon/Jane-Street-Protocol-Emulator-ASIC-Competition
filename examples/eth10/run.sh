#!/usr/bin/env bash
# 10BASE-T transmit lane end to end: tools/tsec/tests/targets/eth10_tx_window.tse
# compiled, loaded into the Verilated full chip, the frames of frames.expect
# staged in the data window and sent, then judged by the 10BASE-T judge from the pins.
#
#   examples/eth10/run.sh [OUT]   (default build/e2e/eth10)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/build/e2e/eth10}"
SPEC=eth10_tx_window
FRAMES="$HERE/frames.expect"
cd "$ROOT"
mkdir -p "$OUT"

step() { printf '== %s\n' "$*"; }
die() { echo "FAIL e2e-eth10: $*"; exit 1; }

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile $SPEC"
python3 -m tools.tsec build "tools/tsec/tests/targets/$SPEC.tse" --emit img,lst,report,json -o "$OUT/tsec" \
    >"$OUT/tsec.log" 2>&1 || { cat "$OUT/tsec.log"; die "tsec build"; }
grep -E "^RESULT" "$OUT/tsec.log"

step "load, run, stage and send the frames"
python3 "$HERE/eth_demo.py" --sim "$SIM --pin-server --vcd $OUT/pins.vcd --vcd-hold" --tsec "$OUT/tsec" "$SPEC" \
    --vcd "$OUT/pins.vcd" --line "$OUT/line.vcd" --frames "$FRAMES" >"$OUT/demo.log" 2>&1 || true
grep -v "^tse_sim:" "$OUT/demo.log"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"

step "judge the line (10BASE-T)"
python3 -m verif.judges eth10 "$OUT/line.vcd" --map td=line --expect "$FRAMES" >"$OUT/judge.log" || true
cat "$OUT/judge.log"
grep -q "^PASS eth10" "$OUT/judge.log" || die "10BASE-T judge"
echo "PASS e2e-eth10: $(grep -vc '^#' "$FRAMES") frame(s) judged"
