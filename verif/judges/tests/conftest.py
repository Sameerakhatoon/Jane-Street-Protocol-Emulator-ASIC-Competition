# SPDX-License-Identifier: Apache-2.0
import os

from hypothesis import HealthCheck, settings

settings.register_profile("judges", max_examples=int(os.environ.get("JUDGES_HYP_EXAMPLES", "25")),
                          deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
settings.load_profile("judges")
