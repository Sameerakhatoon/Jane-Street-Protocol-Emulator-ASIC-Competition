# SPDX-License-Identifier: Apache-2.0
# ISA and generators: tables in isa/, generators in tools/, assembler and
# timing API in tools/tseasm, sequencer spec in src/tse_seq_spec.sv with its
# test in verif/seqspec.
PY ?= python3
ISA_GEN := src/tse_isa_pkg.sv src/tse_regs_pkg.sv tools/tseasm/isa_table.py tools/tseasm/regs_table.py docs/isa.md
PYTEST := HYPOTHESIS_STORAGE_DIRECTORY=build/hypothesis $(PY) -m pytest -q -p no:cacheprovider

.PHONY: isa-gen isa-check isa-lint isa-test isa-seqspec

isa-gen:
	$(PY) tools/gen_isa.py isa/tse_isa.yaml src/tse_isa_pkg.sv tools/tseasm/isa_table.py
	$(PY) tools/gen_regs.py isa/tse_regs.yaml src/tse_regs_pkg.sv tools/tseasm/regs_table.py
	$(PY) tools/gen_isa_doc.py isa/tse_isa.yaml isa/tse_regs.yaml docs/isa.md

# regenerate and fail if any generated file differs from the committed one
isa-check: isa-gen
	git diff --exit-code -- $(ISA_GEN)

isa-lint: isa-check
	verilator --lint-only -Wall --top-module tse_pkg_lint src/tse_isa_pkg.sv src/tse_regs_pkg.sv isa/tse_pkg_lint.sv
	verilator --lint-only -Wall --top-module tse_seq_spec src/tse_isa_pkg.sv src/tse_regs_pkg.sv src/tse_seq_spec.sv

isa-test:
	$(PYTEST) tools/tseasm/tests

# the spec against the cost model on random programs, the timing API against the spec
# (SEQSPEC_TESTS, SEQSPEC_CYCLES, SEQSPEC_SEED, SEQSPEC_TIMING_CASES scale the run)
isa-seqspec:
	$(PYTEST) verif/seqspec

lint: isa-lint
test: isa-test isa-seqspec
