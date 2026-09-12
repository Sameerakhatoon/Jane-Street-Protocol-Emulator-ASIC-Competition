# Single entry point. Each area adds its targets in mk/<area>.mk.
SHELL := /bin/bash
.PHONY: all lint test check
all: check
-include mk/*.mk
check: lint test
