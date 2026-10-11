#!/usr/bin/env bash
# Nightly speed record: one machine-readable BENCH line per top and a floor
# on CPU-time cycles per second (steadier than wall time on a busy machine).
# Floors are set well under the measured speed so only a real regression
# trips them; override with BENCH_FLOOR_ECHO and BENCH_FLOOR_CHIP.
. "$(dirname "$0")/lib.sh"

floor_echo="${BENCH_FLOOR_ECHO:-2000000}"
floor_chip="${BENCH_FLOOR_CHIP:-150000}"

check() {   # check <top> <cycles> <floor>
    local line cps
    line="$(./build/$1/tse_sim --bench "$2" | grep '^BENCH ')" || fail "$1: no BENCH line"
    echo "$line"
    [[ "$line" =~ ^BENCH\ top=$1\ cycles=$2\ wall_s=[0-9.]+\ cpu_s=[0-9.]+\ cps=[0-9]+\ cpu_cps=([0-9]+)$ ]] \
        || fail "$1: malformed: $line"
    cps="${BASH_REMATCH[1]}"
    [ "$cps" -ge "$3" ] || fail "$1: $cps cycles/s of CPU time is under the floor $3"
    summary="$summary $1 $cps"
}

summary=""
build_top echo
check echo 3000000 "$floor_echo"
dir="$(make -s --no-print-directory print-RTL_DIR)"
if [ -f "${dir}/tse_chip.sv" ]; then
    build_top chip
    check chip 1000000 "$floor_chip"
fi
pass "cpu cycles/s:$summary"
