# ISA and generators: tables in isa/, generators in tools/, assembler in tools/tseasm.
PY ?= python3
ISA_GEN := src/tse_isa_pkg.sv src/tse_regs_pkg.sv tools/tseasm/isa_table.py tools/tseasm/regs_table.py

.PHONY: isa-gen isa-check isa-lint isa-test

isa-gen:
	$(PY) tools/gen_isa.py isa/tse_isa.yaml src/tse_isa_pkg.sv tools/tseasm/isa_table.py
	$(PY) tools/gen_regs.py isa/tse_regs.yaml src/tse_regs_pkg.sv tools/tseasm/regs_table.py

# regenerate and fail if any generated file differs from the committed one
isa-check: isa-gen
	git diff --exit-code -- $(ISA_GEN)

isa-lint: isa-check
	verilator --lint-only -Wall --top-module tse_pkg_lint src/tse_isa_pkg.sv src/tse_regs_pkg.sv isa/tse_pkg_lint.sv

isa-test:
	HYPOTHESIS_STORAGE_DIRECTORY=build/hypothesis $(PY) -m pytest -q -p no:cacheprovider tools/tseasm/tests

lint: isa-lint
test: isa-test
