from __future__ import annotations

import json

import pytest
from benchmarks.drivers import (
    DirectValidationDriver,
    OpenAICapabilityValidationDriver,
    OpenAISandboxValidationDriver,
)
from benchmarks.validation import run_stateful_reference_scenario


@pytest.mark.asyncio
async def test_stateful_reference_scenario_is_equivalent_across_supported_drivers() -> None:
    direct = await run_stateful_reference_scenario(DirectValidationDriver())
    openai_sandbox = await run_stateful_reference_scenario(OpenAISandboxValidationDriver())
    openai_capability = await run_stateful_reference_scenario(OpenAICapabilityValidationDriver())

    assert openai_sandbox.equivalence_json() == direct.equivalence_json()
    assert openai_capability.equivalence_json() == direct.equivalence_json()
    assert openai_sandbox.correctness_checksum == direct.correctness_checksum
    assert openai_capability.correctness_checksum == direct.correctness_checksum

    events = {event.name: json.loads(event.payload_json) for event in direct.events}
    assert events["create"] == {
        "approved_environment": [],
        "cwd": "/workspace",
        "revision": 0,
    }
    assert events["inspect"]["stdout"] == "/workspace/project\nalpha\nbeta\ngamma\n"
    assert events["patch"]["revision"] == 3
    assert events["stale_write"] == {
        "category": "conflict",
        "code": "stale_content",
    }
    assert events["unsupported_execute"]["failure_code"] == "command_not_found"
    assert events["unsupported_execute"]["exit_code"] == 127
    assert events["checkpoint"]["cwd"] == "/workspace/project"
    assert events["checkpoint"]["approved_environment"] == [["MODE", "base"]]
    assert events["closed_read"] == {
        "category": "conflict",
        "code": "session_closed",
    }
    assert events["resume"]["identity_changed"] is True
    assert events["resume"]["state"] == {
        "approved_environment": [["MODE", "base"]],
        "cwd": "/workspace/project",
        "revision": 3,
    }
    assert events["continued_execute"]["stdout"] == "one\nthree\n"
    assert events["fork_identities"]["all_distinct"] is True
    assert events["first_fork_read"]["content"] == "first"
    assert events["second_fork_read"]["content"] == "second"
    assert events["resumed_fork_absent"] == {
        "category": "not_found",
        "code": "path_not_found",
    }
    assert events["live_cleanup"] == {
        "first_fork": True,
        "resumed": True,
        "second_fork": True,
    }
    assert events["snapshot_cleanup"] == {"count": 0}


@pytest.mark.asyncio
async def test_stateful_reference_scenario_replays_deterministically() -> None:
    first = await run_stateful_reference_scenario(DirectValidationDriver())
    second = await run_stateful_reference_scenario(DirectValidationDriver())

    assert second.equivalence_json() == first.equivalence_json()
    assert second.correctness_checksum == first.correctness_checksum
