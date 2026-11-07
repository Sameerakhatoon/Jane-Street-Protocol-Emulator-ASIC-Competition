# SPDX-License-Identifier: Apache-2.0
"""Test paths: host/ for tse_host and tse_rp2, the repository root for tools.tseasm."""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOST = os.path.dirname(HERE)
ROOT = os.path.dirname(HOST)
for p in (HOST, ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

collect_ignore = ["rtl", "shim"]
