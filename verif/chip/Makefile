# SPDX-License-Identifier: Apache-2.0
# cocotb tests (Icarus, cocotb 2.1), two benches:
#   TB=chip  the whole chip through its pins: loader byte streams, the run-mode
#            SPI target, self-test with injected SRAM faults, fallback store, data
#            window, compiled programs judged on the pins. The source list is the
#            full-chip one from info_chip.yaml, with the SRAM model instead of the
#            black box.
#   TB=pmem  tse_prog_mem alone with stub lanes and host: data slots, host turn,
#            fallback fetches, random traffic against a shadow memory.
TOP      := $(abspath ../..)
LANES    ?= 2
TB       ?= chip
SOURCES  := $(shell sed -n '/^ *source_files:/,/^[^ ]/s/^ *- *"\([^"]*\)".*/\1/p' $(TOP)/info_chip.yaml)

SIM              ?= icarus
TOPLEVEL_LANG    := verilog
ifeq ($(TB),pmem)
VERILOG_SOURCES  := $(addprefix $(TOP)/src/,tse_prog_mem.sv tse_slot_arb.sv tse_cfg_bank.sv tse_sram_512x32.sv tse_sram_model.v)
COCOTB_TOPLEVEL  := tse_prog_mem
COCOTB_TEST_MODULES := test_pmem
else
VERILOG_SOURCES  := $(addprefix $(TOP)/src/,$(filter-out tse_sram_bb.v,$(SOURCES))) $(TOP)/src/tse_sram_model.v
COCOTB_TOPLEVEL  := tse_chip
COCOTB_TEST_MODULES := test_loader,test_spi,test_memory,test_lane
endif
COMPILE_ARGS     += -g2012 -DSIM -P$(COCOTB_TOPLEVEL).LANES=$(LANES)
SIM_BUILD        := $(TOP)/build/verif-chip/$(TB)_L$(LANES)
COCOTB_RESULTS_FILE := $(TOP)/build/verif-chip/results_$(TB)_L$(LANES).xml

export LANES
export PYTHONPATH := $(TOP)/host:$(TOP):$(PYTHONPATH)
export PYTHONDONTWRITEBYTECODE := 1

include $(shell cocotb-config --makefiles)/Makefile.sim
