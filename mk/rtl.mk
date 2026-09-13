# RTL: lint gate, the template cocotb test, SystemVerilog testbenches, area.
# make area needs LIB or LIB_SRC (see tools/pd/area.sh); CAL_CRC is optional.
.PHONY: rtl-lint rtl-test rtl-sim area

lint: rtl-lint
test: rtl-test rtl-sim

rtl-lint:
	bash test/rtl/lint.sh

rtl-test:
	cd test && $(MAKE)
	@! grep -q '<failure' test/results.xml

rtl-sim:
	bash test/rtl/run-all.sh

area:
	bash tools/pd/area-all.sh
