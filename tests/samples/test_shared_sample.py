from __future__ import annotations

from io import StringIO

import pytest
from samples.shared.__main__ import run_interactive_sample


@pytest.mark.asyncio
async def test_interactive_sample_runs_without_model_or_network() -> None:
    output = StringIO()
    errors = StringIO()

    await run_interactive_sample(
        input_stream=StringIO(
            "pwd\nmkdir demo\necho hello > demo/message.txt\ncat demo/message.txt\nexit\n"
        ),
        output_stream=output,
        error_stream=errors,
    )

    assert "/workspace\n" in output.getvalue()
    assert "hello\n" in output.getvalue()
    assert errors.getvalue() == ""
