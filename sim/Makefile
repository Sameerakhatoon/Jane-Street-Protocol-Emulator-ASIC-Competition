# Simulation harness: one tse_sim binary per Verilated top.
#   make                 TOP=echo (stand-in), build/echo/tse_sim
#   make TOP=uart        UART test Tiny Tapeout top from RTL_DIR
#   make TOP=chip        full chip (tse_chip) from RTL_DIR
#   make TOP=chip pgo    profile-guided model, build/chip-pgo/tse_sim
#   make unit            adapter unit tests, build/unit/
#   make pins            regenerate bridge/pins_gen.h from the pinout tables
SHELL      := /bin/bash
RTL_DIR    ?= ../src
TOP        ?= echo

# Packages first so the modules can import them; every other block of the
# chip except the Tiny Tapeout tops, plus the SIM-selected SRAM model.
CHIP_SV    = $(notdir $(wildcard $(RTL_DIR)/*_pkg.sv)) \
             $(filter-out %_pkg.sv tt_um_%,$(notdir $(wildcard $(RTL_DIR)/*.sv))) \
             $(notdir $(wildcard $(RTL_DIR)/*.v))
UART_SV    = tt_um_tse_sk.sv tse_rst_sync.sv tse_sync.sv tse_nco.sv tse_uart_tx.sv tse_uart_rx.sv
ECHO_SV    = rtl/tt_um_uart_echo.sv rtl/sim_jtag_tap.sv rtl/sim_swd_dp.sv

ifeq ($(TOP),echo)
RTL_SRC    = $(ECHO_SV)
RTL_TOP    = tt_um_uart_echo
TOP_DEF    = -DTSE_TOP_ECHO
VL_WARN    = -Wall
else ifeq ($(TOP),uart)
RTL_SRC    = $(addprefix $(RTL_DIR)/,$(UART_SV))
RTL_TOP    = tt_um_tse_sk
TOP_DEF    = -DTSE_TOP_UART
VL_WARN    = -Wno-fatal
else ifeq ($(TOP),chip)
RTL_SRC    = $(addprefix $(RTL_DIR)/,$(CHIP_SV))
RTL_TOP    = tse_chip
TOP_DEF    = -DTSE_TOP_CHIP
VL_WARN    = -Wno-fatal -DSIM
else
$(error TOP must be echo, uart or chip)
endif

VERILATOR ?= verilator
VROOT     := $(shell $(VERILATOR) --getenv VERILATOR_ROOT)
CXX        = g++
CXXFLAGS   = -O2 -g -std=c++17 -Wall -Wextra -Ibridge
LDXFLAGS  ?=
VLINC      = -isystem $(VL) -isystem $(VROOT)/include -isystem $(VROOT)/include/vltstd
# compat/lz4.h only when the lz4 -dev header is missing; the runtime is always there
LZ4_INC    = $(if $(wildcard /usr/include/lz4.h),,-I$(CURDIR)/compat)
LDLIBS     = -l:liblz4.so.1 -lz -lpthread -latomic

# Model build knobs. VARIANT keeps experiments apart, for example
#   make TOP=chip VARIANT=-l1 VL_OPT="-O3 --x-assign fast --x-initial fast -GLANES=1"
# --threads N made the full chip slower here, so it is not used.
VL_OPT    ?= -O3 --x-assign fast --x-initial fast
VL_CFLAGS ?= -O3 -march=native
VARIANT   ?=
TRACE     ?= 1
VL_TRACE   = $(if $(filter 1,$(TRACE)),--trace-fst,)
TRACE_DEF  = $(if $(filter 1,$(TRACE)),,-DTSE_NO_TRACE)

B          = build/$(TOP)$(VARIANT)
VL         = $(B)/vl
OBJ        = build/obj
HDRS       = $(wildcard bridge/*.h)
ADAPTERS   = $(addprefix $(OBJ)/,vcd_pins.o pins.o uart_pty.o eth_tap.o pcap.o serprog.o \
             wired_and.o rbb.o can_bits.o can_node.o usbip.o)
UNIT       = $(patsubst tests/%.cpp,build/unit/%,$(wildcard tests/unit_*.cpp))

all: $(B)/tse_sim

$(B)/tse_sim: $(B)/main.o $(B)/harness.o $(ADAPTERS) $(VL)/Vtop__ALL.a
	$(CXX) $(CXXFLAGS) $(LDXFLAGS) $^ $(VL)/libverilated.a -o $@ $(LDLIBS)

$(VL)/Vtop__ALL.a: $(RTL_SRC) Makefile
	@mkdir -p $(VL)
	$(VERILATOR) --cc --build -j 2 --prefix Vtop --top-module $(RTL_TOP) --Mdir $(VL) \
	    $(VL_TRACE) --timescale 1ps/1ps $(VL_OPT) \
	    -CFLAGS "$(VL_CFLAGS) -std=c++17 $(LZ4_INC)" $(VL_WARN) $(RTL_SRC) > $(B)/verilator.log 2>&1 \
	    || { tail -30 $(B)/verilator.log; exit 1; }

$(B)/harness.o: bridge/harness.cpp $(HDRS) $(VL)/Vtop__ALL.a
	$(CXX) $(CXXFLAGS) $(TRACE_DEF) $(VLINC) -c $< -o $@

$(B)/main.o: bridge/main.cpp $(HDRS)
	@mkdir -p $(B)
	$(CXX) $(CXXFLAGS) $(TOP_DEF) -c $< -o $@

$(OBJ)/%.o: bridge/%.cpp $(HDRS)
	@mkdir -p $(OBJ)
	$(CXX) $(CXXFLAGS) -c $< -o $@

# Profile-guided model: build with -fprofile-generate, train with --bench,
# then compile the model again in the same folder with the profile.
PGO        = build/$(TOP)-pgo
pgo:
	rm -rf $(PGO)
	$(MAKE) TOP=$(TOP) VARIANT=-pgo VL_CFLAGS="$(VL_CFLAGS) -fprofile-generate -fprofile-update=single" \
	    LDXFLAGS=-fprofile-generate
	$(PGO)/tse_sim --bench 2000000 >/dev/null
	rm -f $(PGO)/vl/*.o $(PGO)/vl/*.a $(PGO)/tse_sim
	$(MAKE) -C $(PGO)/vl -f Vtop.mk -j 2 Vtop__ALL.a libverilated.a \
	    VM_USER_CFLAGS="$(VL_CFLAGS) -std=c++17 $(LZ4_INC) -fprofile-use -fprofile-correction -Wno-missing-profile" \
	    > $(PGO)/pgo.log 2>&1 || { tail -20 $(PGO)/pgo.log; exit 1; }
	$(CXX) $(CXXFLAGS) $(PGO)/main.o $(PGO)/harness.o $(ADAPTERS) $(PGO)/vl/Vtop__ALL.a \
	    $(PGO)/vl/libverilated.a -o $(PGO)/tse_sim $(LDLIBS)

unit: $(UNIT)

build/unit/%: tests/%.cpp $(ADAPTERS) $(HDRS)
	@mkdir -p build/unit
	$(CXX) $(CXXFLAGS) $< $(ADAPTERS) -o $@

pins:
	python3 gen_pins.py

lint:
	$(VERILATOR) --lint-only -Wall --top-module tt_um_uart_echo $(ECHO_SV)

test:
	bash tests/run-all.sh

clean:
	rm -rf build

print-%:
	@echo $($*)

.PHONY: all pgo unit pins lint test clean
