# Verification: Python reference model, RTL-versus-model lockstep, formal, mutation.
RTL_DIR ?= src
PYTHON  ?= python3
SIM     ?= icarus
HYP_EXAMPLES ?= 100
CI      ?=
VERIF_PY := $(wildcard model/*.py model/tests/*.py verif/lockstep/*.py)
FORMAL_BUILD := build/formal

.PHONY: verif-lint verif-test model-test lockstep formal mcy verif-nightly
lint: verif-lint
test: verif-test

verif-lint:
	$(PYTHON) -m py_compile $(VERIF_PY)
	verilator --lint-only -Wall --top-module seq_harness $(RTL_DIR)/tse_seq.sv verif/lockstep/seq_harness.sv
	verilator --lint-only -Wall -Wno-PROCASSINIT -Wno-SYNCASYNCNET --top-module crc_props -DCAT $(RTL_DIR)/tse_crc.sv formal/crc/crc_props.sv

verif-test: model-test lockstep

model-test:
	$(PYTHON) -m pytest -q -p no:cacheprovider model/tests

lockstep:
	cd verif/lockstep && RTL_DIR=$(abspath $(RTL_DIR)) SIM=$(SIM) HYP_EXAMPLES=$(HYP_EXAMPLES) CI=$(CI) $(PYTHON) run.py

formal:
	rm -rf $(FORMAL_BUILD)/crc && mkdir -p $(FORMAL_BUILD)/crc
	cp formal/crc/* $(RTL_DIR)/tse_crc.sv $(FORMAL_BUILD)/crc/
	cd $(FORMAL_BUILD)/crc && sby -f crc.sby

mcy:
	rm -rf $(FORMAL_BUILD)/mcy_crc && mkdir -p $(FORMAL_BUILD)/mcy_crc
	cp formal/mcy_crc/* formal/crc/crc_props.sv $(RTL_DIR)/tse_crc.sv $(FORMAL_BUILD)/mcy_crc/
	cd $(FORMAL_BUILD)/mcy_crc && mcy init && mcy run -j$$(nproc) && mcy status

verif-nightly:
	$(MAKE) lockstep HYP_EXAMPLES=2000 CI=false
	$(MAKE) mcy
