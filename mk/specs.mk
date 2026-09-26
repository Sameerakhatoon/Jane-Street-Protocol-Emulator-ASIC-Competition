# Protocol specs: specs/*.tse and their tests in specs/tests.
SPECS_SRC := $(wildcard specs/*.tse) $(wildcard specs/tests/*.sh) specs/tests/expected-outcomes.txt

.PHONY: specs-lint specs-test

# plain ASCII, no tabs, no trailing blanks
specs-lint:
	@! LC_ALL=C grep -nP '[^\x00-\x7f]' $(SPECS_SRC) || { echo "non-ASCII"; exit 1; }
	@! grep -n "$$(printf '\t')" $(SPECS_SRC) || { echo "tabs"; exit 1; }
	@! grep -n ' $$' $(SPECS_SRC) || { echo "trailing blanks"; exit 1; }

specs-test:
	bash specs/tests/run-all.sh

lint: specs-lint
test: specs-test
