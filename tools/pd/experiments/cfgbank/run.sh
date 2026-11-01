#!/usr/bin/env bash
# tools/pd/experiments/cfgbank/run.sh: harden the latch configuration bank alone in a 1x1 tile
# at 60 MHz and print the three-corner timing table. The bank RTL is src/tse_cfg_bank.sv.
# It builds a throwaway Tiny Tapeout project in runs/cfgbank/proj from the repository's own
# info.yaml and src/config.json, so the tile size, pins and power grid match the real flow.
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
cd "$here/../../../.."
source tools/pd/env.sh
root=$PWD
p=runs/cfgbank/proj
rm -rf runs/cfgbank
mkdir -p $p/src runs/cfgbank/run
cp "$here"/tt_um_cfgbank_exp.sv src/tse_cfg_bank.sv $p/src/
ln -s "$root/tt" $p/tt
python - $p <<'PY'
import json, sys, yaml
p = sys.argv[1]
d = yaml.safe_load(open("info.yaml"))
d["project"].update(title="cfgbank latch timing", author="tse", description="latch bank",
                    top_module="tt_um_cfgbank_exp", tiles="1x1", clock_hz=60000000,
                    source_files=["tt_um_cfgbank_exp.sv", "tse_cfg_bank.sv"])
yaml.safe_dump(d, open(f"{p}/info.yaml", "w"), sort_keys=False)
c = json.load(open("src/config.json"))
c["CLOCK_PERIOD"] = 16.67
json.dump(c, open(f"{p}/src/config.json", "w"), indent=2)
PY
start=$(date +%s)
(cd $p && ./tt/tt_tool.py --create-user-config --ihp)
python -m librelane -j "$PD_JOBS" --pdk-root "$PDK_ROOT" --docker-no-tty --dockerized \
    --pdk ihp-sg13cmos5l --manual-pdk --run-tag cfgbank --force-run-dir runs/cfgbank/run \
    $p/src/config_merged.json 2>&1 | tee runs/cfgbank/harden.log
python tools/pd/corners.py runs/cfgbank/run/final/metrics.json || true
echo "wall time: $(( ($(date +%s) - start) / 60 )) min"

# The flow's STA cannot see the latch paths (see latch_sta.py); time them separately.
python "$here/latch_sta.py" runs/cfgbank/run runs/cfgbank/sta
image=ghcr.io/librelane/librelane:$(python -c "import librelane; print(librelane.__version__)")
for t in runs/cfgbank/sta/*.tcl; do
    docker run --rm -u "$(id -u):$(id -g)" -v "$PWD:$PWD" -v "$PDK_ROOT:$PDK_ROOT" -w "$PWD" "$image" \
        sta -exit "$t" > "${t%.tcl}.log" 2>&1
    echo "$(basename "${t%.tcl}"): $(grep -ho "There are [0-9]* unconstrained endpoints" "${t%.tcl}.log") $(tail -1 "${t%.tcl}.log")"
done
