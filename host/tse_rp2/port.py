# SPDX-License-Identifier: Apache-2.0
"""MicroPython time functions, or the test shim's simulated time under CPython."""
import time

if hasattr(time, "sleep_us"):
    sleep_us = time.sleep_us
    ticks_ms = time.ticks_ms
    ticks_diff = time.ticks_diff
else:
    from rp2 import sleep_us, ticks_ms, ticks_diff  # noqa: F401  (the CPython shim)
