# Gate-level simulation with SDF: the routed netlist of a full-chip run (final/nl), the run's
# SDF for one corner, the PDK's cell models and IHP's own 512x16 SRAM model (both with specify
# blocks, so cells, macros and wires get their signoff delays), driven by the chip tests.
# Icarus does not run $setup/$hold checks, so a timing failure shows up as a wrong value at
# the pins, not as a timing-check message. It also cannot annotate wires ending on the SRAM's
# vector pins (sdf_for_icarus.py drops those, at most about 25 ps) or the ifnone paths of some
# cells, which only apply while a select input is X; the SRAM macros get their own SDF files.
# Annotating takes Icarus about 40 minutes per corner before the test starts.
# usage: make -C verif/glsdf [RUN=runs/rtl6c/proj/runs/wokwi] [CORNER=nom_typ_1p20V_25C]
#        [GL_TEST=test_01_random_images_load] [GL_MODULES=test_loader]
# corners: nom_fast_1p32V_m40C nom_typ_1p20V_25C nom_slow_1p08V_125C
TOP      := $(abspath ../..)
RUN      ?= runs/rtl6c/proj/runs/wokwi
CORNER   ?= nom_typ_1p20V_25C
RUN_DIR  := $(abspath $(TOP)/$(RUN))
NETLIST  := $(RUN_DIR)/final/nl/tt_um_tse_sk_chip.nl.v
SDF      := $(RUN_DIR)/final/sdf/$(CORNER)/tt_um_tse_sk_chip__$(CORNER).sdf
CELLS    := $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell/verilog
SRAM     := $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_sram/verilog
BUILD    := $(TOP)/build/glsdf/$(notdir $(patsubst %/proj/runs/wokwi,%,$(RUN_DIR)))/$(CORNER)
GL_MODULES ?= test_loader
GL_TEST  ?= test_01_random_images_load

SIM              ?= icarus
TOPLEVEL_LANG    := verilog
VERILOG_SOURCES  := $(CELLS)/sg13cmos5l_stdcell.v $(CELLS)/sg13cmos5l_udp.v \
                    $(SRAM)/RM_IHPSG13_1P_core_behavioral_bm_bist.v $(SRAM)/RM_IHPSG13_1P_512x16_c2_bm_bist.v \
                    $(NETLIST) $(TOP)/verif/glsdf/tse_chip_sdf.v
COMPILE_ARGS     += -g2012 -gspecify -ginterconnect
COCOTB_PLUSARGS  += +sdf=$(BUILD)/icarus.sdf +sdf_lo=$(BUILD)/sram_u_macro_lo.sdf +sdf_hi=$(BUILD)/sram_u_macro_hi.sdf
COCOTB_TOPLEVEL  := tse_chip
COCOTB_TEST_MODULES := $(GL_MODULES)
COCOTB_TEST_FILTER  := $(GL_TEST)
SIM_BUILD        := $(BUILD)/sim
COCOTB_RESULTS_FILE := $(BUILD)/results.xml

export LANES := 2
export PYTHONPATH := $(TOP)/verif/chip:$(TOP)/host:$(TOP):$(PYTHONPATH)
export PYTHONDONTWRITEBYTECODE := 1

include $(shell cocotb-config --makefiles)/Makefile.sim

$(COCOTB_RESULTS_FILE): $(BUILD)/icarus.sdf

$(BUILD)/icarus.sdf: $(SDF) sdf_for_icarus.py
	mkdir -p $(BUILD)
	python3 sdf_for_icarus.py $< $(BUILD)
