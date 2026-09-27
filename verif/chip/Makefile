# cocotb tests of the whole chip through its pins (Icarus, cocotb 2.1):
# loader byte streams and the run-mode SPI target. The source list is the
# full-chip one from info_chip.yaml, with the SRAM model instead of the
# black box. make LANES=4 runs the 4-lane build.
TOP      := $(abspath ../..)
LANES    ?= 3
SOURCES  := $(shell sed -n '/^ *source_files:/,/^[^ ]/s/^ *- *"\([^"]*\)".*/\1/p' $(TOP)/info_chip.yaml)

SIM              ?= icarus
TOPLEVEL_LANG    := verilog
VERILOG_SOURCES  := $(addprefix $(TOP)/src/,$(filter-out tse_sram_bb.v,$(SOURCES))) $(TOP)/src/tse_sram_model.v
COMPILE_ARGS     += -g2012 -DSIM -Ptse_chip.LANES=$(LANES)
COCOTB_TOPLEVEL  := tse_chip
COCOTB_TEST_MODULES := test_loader,test_spi
SIM_BUILD        := $(TOP)/build/verif-chip/L$(LANES)
COCOTB_RESULTS_FILE := $(TOP)/build/verif-chip/results_L$(LANES).xml

export LANES
export PYTHONPATH := $(TOP)/host:$(TOP):$(PYTHONPATH)
export PYTHONDONTWRITEBYTECODE := 1

include $(shell cocotb-config --makefiles)/Makefile.sim
