# SPDX-License-Identifier: Apache-2.0
# Makefile
# See https://docs.cocotb.org/en/stable/quickstart.html for more info
# The top module and the source list are read from ../info.yaml, so the two
# can never drift apart.

# defaults
SIM ?= icarus
FST ?= -fst # Use more efficient FST format
TOPLEVEL_LANG ?= verilog
SRC_DIR = $(PWD)/../src
INFO ?= $(PWD)/../info.yaml
TT_TOP := $(shell sed -n 's/^ *top_module: *"\([^"]*\)".*/\1/p' $(INFO))
PROJECT_SOURCES := $(shell sed -n '/^ *source_files:/,/^[^ ]/s/^ *- *"\([^"]*\)".*/\1/p' $(INFO))

ifneq ($(GATES),yes)

# RTL simulation:
SIM_BUILD				= sim_build/rtl
# the full chip's SRAM black box is replaced by its behavioural model
VERILOG_SOURCES += $(addprefix $(SRC_DIR)/,$(patsubst tse_sram_bb.v,tse_sram_model.v,$(PROJECT_SOURCES)))
COMPILE_ARGS    += -g2012 -DSIM

else

# Gate level simulation:
SIM_BUILD				= sim_build/gl
COMPILE_ARGS    += -DGL_TEST
COMPILE_ARGS    += -DFUNCTIONAL
COMPILE_ARGS    += -DSIM
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_io/verilog/sg13cmos5l_io.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_stdcell.v
# Template fix: the flop models instantiate UDPs (ihp_dff_r and friends) that live here.
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog/sg13cmos5l_udp.v

# IHP's SRAM models, for the two program SRAM macros of the full chip (unused
# by the UART design)
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram/verilog/RM_IHPSG13_1P_core_behavioral_bm_bist.v
VERILOG_SOURCES += $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram/verilog/RM_IHPSG13_1P_512x16_c2_bm_bist.v

# this gets copied in by the GDS action workflow
VERILOG_SOURCES += $(PWD)/gate_level_netlist.v

endif

# Allow sharing configuration between design and testbench via `include`:
COMPILE_ARGS 		+= -I$(SRC_DIR)
COMPILE_ARGS 		+= -DTT_TOP=$(TT_TOP)

# Include the testbench sources:
VERILOG_SOURCES += $(PWD)/tb.v
TOPLEVEL = tb

# List test modules to run, separated by commas and without the .py suffix:
# test.py for the UART design, test_chip.py for any other top (the full chip).
ifeq ($(TT_TOP),tt_um_tse_sk)
COCOTB_TEST_MODULES = test
else
COCOTB_TEST_MODULES = test_chip
endif

# include cocotb's make rules to take care of the simulator setup
include $(shell cocotb-config --makefiles)/Makefile.sim
