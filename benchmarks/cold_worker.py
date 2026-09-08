"""Fresh-interpreter ready probe used by the cold-process benchmark."""

from __future__ import annotations

import asyncio
import json
import sys
import time

from benchmarks.drivers import (
    DirectValidationDriver,
    OpenAICapabilityValidationDriver,
    OpenAISandboxValidationDriver,
)
from benchmarks.profiles import load_profile
from benchmarks.validation import ProductValidationDriver


async def _main(driver_name: str, profile_name: str) -> None:
    factories = {
        "direct": DirectValidationDriver,
        "openai_sandbox": OpenAISandboxValidationDriver,
        "openai_capability": OpenAICapabilityValidationDriver,
    }
    factory = factories.get(driver_name)
    if factory is None:
        raise ValueError(f"unknown driver: {driver_name}")
    driver: ProductValidationDriver = factory()
    profile = load_profile(profile_name)
    session = await driver.create(profile.create_request("cold-process"))
    state = await session.state()
    ready_ns = time.perf_counter_ns()
    print(
        json.dumps(
            {
                "correctness_checksum": (
                    f"{state.revision}:{state.cwd}:{state.approved_environment}"
                ),
                "ready_ns": ready_ns,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ),
        flush=True,
    )


if __name__ == "__main__":
    asyncio.run(_main(sys.argv[1], sys.argv[2]))
