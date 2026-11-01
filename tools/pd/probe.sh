#!/usr/bin/env bash
# tools/pd/probe.sh: short run that stops after global routing, for floorplan and congestion experiments.
# usage: tools/pd/probe.sh [config.json]   (default src/config_merged.json; run in runs/probe)
set -euo pipefail
cd "$(dirname "$0")/../.."
source tools/pd/env.sh
cfg=${1:-src/config_merged.json}
[ "$cfg" = src/config_merged.json ] && ./tt/tt_tool.py --create-user-config --ihp
rm -rf runs/probe && mkdir -p runs/probe
python -m librelane -j "$PD_JOBS" --pdk-root "$PDK_ROOT" --docker-no-tty --dockerized \
    --pdk ihp-sg13cmos5l --manual-pdk --run-tag probe --force-run-dir runs/probe \
    --to OpenROAD.GlobalRouting "$cfg" 2>&1 | tee harden-probe.log
grep -h -A12 "Final congestion report" runs/probe/*-openroad-globalrouting/*.log | tee -a harden-probe.log || true
