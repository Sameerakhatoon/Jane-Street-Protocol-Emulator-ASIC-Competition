#!/usr/bin/env bash
# SPI mode 0 lanes end to end. target: spi_target_mode0_css114 against a
# stand-in controller on the input pins. controller: spi_controller_mode0,
# built with --pad-skew 0 (its SCK and MOSI edges share an instant, so the
# default skew between two pads breaks the edge budget), against a stand-in
# flash. Each run is judged by the SPI judge on the pins-only VCD.
#
#   examples/spi/run.sh target|controller [OUT]   (default build/e2e/spi-ROLE)
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
ROLE="${1:?target or controller}"
OUT="${2:-$ROOT/build/e2e/spi-$ROLE}"
cd "$ROOT"
mkdir -p "$OUT"

step() { printf '== %s\n' "$*"; }
die() { echo "FAIL e2e-spi-$ROLE: $*"; exit 1; }

case "$ROLE" in
    target)
        SPEC=spi_target_mode0_css114; OPTS=()
        JUDGE=(--map cs=ui_in4 --map clk=ui_in5 --map mosi=ui_in6 --map miso=uo_out1) ;;
    controller)
        SPEC=spi_controller_mode0; OPTS=(--pad-skew 0)
        JUDGE=(--map cs=uio_out4 --map clk=uio_out5 --map mosi=uio_out6 --map miso=ui_in7) ;;
    *) die "role must be target or controller" ;;
esac

step "simulator (full chip)"
make -s --no-print-directory -C sim TOP=chip >"$OUT/sim-build.log" 2>&1 || { tail -20 "$OUT/sim-build.log"; die "sim build"; }
SIM="$ROOT/sim/build/chip/tse_sim"

step "compile specs/$SPEC.tse ${OPTS[*]}"
python3 -m tools.tsec build "specs/$SPEC.tse" "${OPTS[@]}" --emit img,lst,report,json -o "$OUT/tsec" \
    >"$OUT/tsec.log" 2>&1 || { cat "$OUT/tsec.log"; die "tsec build"; }
grep -E "^RESULT" "$OUT/tsec.log"

VCD="$OUT/spi.vcd"
step "load, run, one SPI exchange"
python3 "$HERE/spi_demo.py" --sim "$SIM --pin-server --vcd $VCD --vcd-hold" --tsec "$OUT/tsec" "$SPEC" \
    --role "$ROLE" >"$OUT/demo.log" 2>&1 || true
grep -v "^tse_sim:" "$OUT/demo.log"
grep -q "^PASS host" "$OUT/demo.log" || die "host side"

step "judge the chip-select frames (SPI mode 0)"
python3 -m verif.judges spi "$VCD" "${JUDGE[@]}" --mode 0 --quiet --expect "$HERE/$ROLE.expect" \
    >"$OUT/judge.log" || true
cat "$OUT/judge.log"
grep -q "^PASS spi" "$OUT/judge.log" || die "SPI judge"
echo "PASS e2e-spi-$ROLE: $(grep -vc '^#' "$HERE/$ROLE.expect") frames judged"
