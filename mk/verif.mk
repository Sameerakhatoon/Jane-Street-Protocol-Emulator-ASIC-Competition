# Verification: Python reference model, RTL-versus-model lockstep (sequencer,
# lane, assist blocks), formal proofs with planted bugs, mutation coverage.
RTL_DIR ?= src
PYTHON  ?= python3
SIM     ?= icarus
HYP_EXAMPLES   ?= 100
LANE_EXAMPLES  ?= 30
BLOCK_EXAMPLES ?= 30
MCY_JOBS ?= 2
MCY_BLOCKS ?= crc stuffer codec
CI      ?=
VERIF_PY := $(wildcard model/*.py model/tests/*.py verif/lockstep/*.py verif/lane/*.py verif/blocks/*.py \
                       formal/*.py formal/mcy/*.py)
FORMAL_BUILD := build/formal
LANE_SV := $(addprefix $(RTL_DIR)/,tse_isa_pkg.sv tse_regs_pkg.sv tse_cfg_bank.sv tse_crc.sv tse_stuffer.sv tse_codec.sv tse_dpll.sv \
             tse_resp_rule.sv tse_nco.sv tse_timer.sv tse_seq.sv tse_lane.sv)

.PHONY: verif-lint verif-test model-test lockstep lane-lockstep block-lockstep verilator-lockstep formal mcy \
        verif-nightly
lint: verif-lint
test: verif-test

verif-lint:
	$(PYTHON) -m py_compile $(VERIF_PY)
	verilator --lint-only -Wall --top-module seq_harness $(RTL_DIR)/tse_isa_pkg.sv $(RTL_DIR)/tse_regs_pkg.sv $(RTL_DIR)/tse_seq.sv verif/lockstep/seq_harness.sv
	verilator --lint-only -Wall --top-module lane_harness $(LANE_SV) verif/lane/lane_harness.sv
	verilator --lint-only -Wall -Wno-PROCASSINIT -Wno-SYNCASYNCNET --top-module crc_props -DCAT $(RTL_DIR)/tse_crc.sv formal/crc/crc_props.sv

verif-test: model-test lockstep lane-lockstep block-lockstep

model-test:
	$(PYTHON) -m pytest -q -p no:cacheprovider model/tests

lockstep:
	cd verif/lockstep && RTL_DIR=$(abspath $(RTL_DIR)) SIM=$(SIM) HYP_EXAMPLES=$(HYP_EXAMPLES) CI=$(CI) $(PYTHON) run.py

lane-lockstep:
	cd verif/lane && RTL_DIR=$(abspath $(RTL_DIR)) SIM=$(SIM) HYP_EXAMPLES=$(LANE_EXAMPLES) CI=$(CI) $(PYTHON) run.py

block-lockstep:
	cd verif/blocks && RTL_DIR=$(abspath $(RTL_DIR)) SIM=$(SIM) HYP_EXAMPLES=$(BLOCK_EXAMPLES) CI=$(CI) $(PYTHON) run.py

# the same lockstep suites on the second simulator, few examples
verilator-lockstep:
	$(MAKE) lockstep SIM=verilator HYP_EXAMPLES=10
	$(MAKE) lane-lockstep SIM=verilator LANE_EXAMPLES=10
	$(MAKE) block-lockstep SIM=verilator BLOCK_EXAMPLES=10

formal:
	$(PYTHON) formal/run_formal.py --rtl $(RTL_DIR) --build $(FORMAL_BUILD)

# one block after the other, MCY_JOBS workers each
mcy:
	set -e; for b in $(MCY_BLOCKS); do \
	  $(PYTHON) formal/mcy/run_mcy.py $$b --rtl $(RTL_DIR) --build $(FORMAL_BUILD) --jobs $(MCY_JOBS); \
	done

verif-nightly:
	$(MAKE) lockstep HYP_EXAMPLES=2000 CI=false
	$(MAKE) lane-lockstep LANE_EXAMPLES=1000 CI=false
	$(MAKE) block-lockstep BLOCK_EXAMPLES=1000 CI=false
	$(MAKE) verilator-lockstep
	$(MAKE) mcy
