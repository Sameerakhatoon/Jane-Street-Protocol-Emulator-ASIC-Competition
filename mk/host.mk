# Host and loader: host/ (portable host library, RP2350 code, command line).
PY ?= python3
HOST_TABLES := host/tse_host/regs_map.py host/tse_rp2/tse_asm_table.py
HOST_PY := $(shell find host -name "*.py" -not -path "*/__pycache__/*")

.PHONY: host-tables host-check host-lint host-test host-rtl

host-tables:
	$(PY) host/gen_tables.py

# re-export and fail if a table differs from the committed one
host-check: host-tables
	git diff --exit-code -- $(HOST_TABLES)

# every file parses (the board files cannot be imported here)
host-lint: host-check
	$(PY) -c "import ast, sys; [ast.parse(open(f).read(), f) for f in sys.argv[1:]]" $(HOST_PY)

host-test:
	HYPOTHESIS_STORAGE_DIRECTORY=build/hypothesis $(PY) -m pytest -q -p no:cacheprovider host/tests

# the streams and the session against the full chip (cocotb, Icarus)
host-rtl:
	$(PY) host/tests/rtl/run_rtl.py

lint: host-lint
test: host-test host-rtl
