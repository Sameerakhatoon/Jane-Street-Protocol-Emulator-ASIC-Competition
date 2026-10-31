# Gate-level simulation: tse_chip mapped onto CMOS5L standard cells by Yosys
# (typical corner Liberty, SRAM macro as a black box), then one whole-chip
# cocotb test on that netlist with the PDK's cell models and the SRAM model.
# It finds what RTL simulation hides: missing resets, X propagation, latches
# that synthesize differently from how they simulate.
# usage: make -C verif/gl [GL_TEST=test_01_random_images_load]
TOP      := $(abspath ../..)
BUILD    := $(TOP)/build/gl
NETLIST  := $(BUILD)/tse_chip_gl.v
CELLS    := $(PDK_ROOT)/ihp-sg13cmos5l/libs.ref/sg13cmos5l_stdcell
LIB      := $(CELLS)/lib/sg13cmos5l_stdcell_typ_1p20V_25C.lib
SOURCES  := $(shell sed -n '/^ *source_files:/,/^[^ ]/s/^ *- *"\([^"]*\)".*/\1/p' $(TOP)/info_chip.yaml)
RTL      := $(addprefix $(TOP)/src/,$(filter-out tse_sram_bb.v tt_um_tse_sk_chip.sv,$(SOURCES)))
DONT_USE := -dont_use sg13cmos5l_lgcp_1 -dont_use sg13cmos5l_slgcp_1 -dont_use sg13cmos5l_sdfbbp_1 \
            -dont_use sg13cmos5l_sighold
GL_TEST  ?= test_01_random_images_load

SIM              ?= icarus
TOPLEVEL_LANG    := verilog
VERILOG_SOURCES  := $(CELLS)/verilog/sg13cmos5l_stdcell.v $(CELLS)/verilog/sg13cmos5l_udp.v \
                    $(TOP)/src/tse_sram_model.v $(NETLIST)
COMPILE_ARGS     += -g2012 -DFUNCTIONAL -DSIM
COCOTB_TOPLEVEL  := tse_chip
COCOTB_TEST_MODULES := test_loader
COCOTB_TEST_FILTER  := $(GL_TEST)
SIM_BUILD        := $(BUILD)/sim
COCOTB_RESULTS_FILE := $(BUILD)/results.xml

export LANES := 2
export PYTHONPATH := $(TOP)/verif/chip:$(TOP)/host:$(TOP):$(PYTHONPATH)
export PYTHONDONTWRITEBYTECODE := 1

include $(shell cocotb-config --makefiles)/Makefile.sim

$(SIM_BUILD)/sim.vvp: $(NETLIST)

$(NETLIST): $(RTL) Makefile
	mkdir -p $(BUILD)
	yosys -q -l $(BUILD)/synth.log -p "read_liberty -lib $(LIB); read_verilog -lib $(TOP)/src/tse_sram_bb.v; \
	    read_verilog -sv $(RTL); hierarchy -top tse_chip -chparam LANES 2; synth -flatten -top tse_chip; \
	    dfflibmap -liberty $(LIB) $(DONT_USE); abc -D 16667 -liberty $(LIB) $(DONT_USE); opt_clean; \
	    setundef -zero; tee -o $(BUILD)/stat.txt stat -liberty $(LIB); write_verilog -noattr $@"
