# Physical design: local LibreLane runs. Needs the venv and PDK under ~/ttsetup and tt-support-tools in tt/.
PD_TOP := $(shell sed -n 's/^ *top_module: *"\([^"]*\)".*/\1/p' info.yaml)
.PHONY: harden harden-probe precheck pd-gl-test pd-lint
lint: pd-lint

pd-lint:
	@for f in tools/pd/*.sh; do bash -n $$f || exit 1; done
	@python3 -m py_compile tools/pd/corners.py

harden:
	tools/pd/harden.sh $(TAG)

precheck:
	tools/pd/precheck.sh

harden-probe:
	tools/pd/probe.sh

# Gate-level test on the last run's netlist (unpowered for CMOS5L), as CI's gl_test does
# (gl-test, in mk/verif.mk, is the Yosys netlist test).
pd-gl-test:
	cp runs/wokwi/final/nl/$(PD_TOP).nl.v test/gate_level_netlist.v
	cd test && rm -rf sim_build/gl results.xml && $(MAKE) GATES=yes
	! grep -q "<failure" test/results.xml
