from __future__ import annotations

import pytest
from evaluations.command_usability.run import (
    EvaluationRuntime,
    assistant_content,
    load_corpus,
)


def test_command_usability_corpus_has_stable_unique_task_ids() -> None:
    scenarios = load_corpus()

    assert [scenario.task_id for scenario in scenarios] == [f"T{index}" for index in range(1, 13)]
    assert all(scenario.seed_files or scenario.task_id in {"T1", "T12"} for scenario in scenarios)


def test_assistant_content_reads_unwrapped_jsonl_message() -> None:
    output = "\n".join(
        (
            '{"type":"session.start","data":{}}',
            '{"type":"assistant.message","data":{"content":"{\\"actions\\":[]}"}}',
        )
    )

    assert assistant_content(output) == '{"actions":[]}'


@pytest.mark.asyncio
async def test_command_usability_runtime_executes_tools_and_oracles() -> None:
    scenarios = load_corpus()
    runtime = EvaluationRuntime(scenarios)
    await runtime.start()
    try:
        written = await runtime.invoke(
            "T1",
            "write_file",
            {
                "path": "/workspace/project/notes.txt",
                "content": "alpha\nbeta\n",
                "create_parents": True,
            },
        )
        assert written["ok"] is True
        assert await runtime.evaluate("T1", "") == (True, ())

        counted = await runtime.invoke(
            "T7",
            "execute",
            {"command": "find /workspace/repo -type f -name '*.py' | wc -l"},
        )
        assert counted["exit_code"] == 0
        assert counted["stdout"] == "3\n"
        assert await runtime.evaluate("T7", "3") == (True, ())

        missing = await runtime.invoke(
            "T7",
            "execute",
            {"command": "python3 -c 'print(3)'"},
        )
        assert missing["failure_code"] == "command_not_found"
    finally:
        await runtime.close()
