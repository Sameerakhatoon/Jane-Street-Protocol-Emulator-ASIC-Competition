# SPDX-License-Identifier: Apache-2.0
# Hygiene, SPDX lines and Check-mode tools: tools/hygiene, tools/spdx.py,
# host/tse_check.
# make hygiene-log RANGE=cmos5l..HEAD checks commit messages.
PY ?= python3
RANGE ?= HEAD
TOOLS_TESTS := host/tse_check/tests tools/hygiene/tests

.PHONY: hygiene hygiene-log tools-test spdx-check

hygiene:
	$(PY) tools/hygiene/check.py

# every source file the project wrote carries an SPDX line (python3 tools/spdx.py --add
# adds the missing ones); the RTL and ISA are left out
spdx-check:
	$(PY) tools/spdx.py

hygiene-log:
	$(PY) tools/hygiene/check.py --commits $(RANGE)

tools-test:
	HYPOTHESIS_STORAGE_DIRECTORY=build/hypothesis $(PY) -m pytest -q -p no:cacheprovider $(TOOLS_TESTS)

lint: hygiene spdx-check
test: tools-test
