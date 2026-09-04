from __future__ import annotations

import os

from hypothesis import settings

settings.register_profile(
    "local",
    max_examples=50,
    stateful_step_count=25,
    deadline=None,
    print_blob=True,
)
settings.register_profile(
    "ci",
    max_examples=100,
    stateful_step_count=30,
    deadline=None,
    print_blob=True,
    derandomize=True,
    database=None,
)
settings.register_profile(
    "extended",
    max_examples=250,
    stateful_step_count=50,
    deadline=None,
    print_blob=True,
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "local"))
