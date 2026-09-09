"""Credential-free interactive MemSandbox sample."""

from __future__ import annotations

import asyncio
import sys
from typing import TextIO

from mem_sandbox.service import CreateSandboxRequest, OwnerId
from samples.shared.cli import run_inspection_cli
from samples.shared.service import create_sample_service

_OWNER_ID = OwnerId("interactive-cli-sample")


async def run_interactive_sample(
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    error_stream: TextIO | None = None,
) -> None:
    """Create an empty sandbox, run its constrained CLI, and close the service."""
    service = create_sample_service()
    try:
        handle = await service.create(CreateSandboxRequest(owner_id=_OWNER_ID))
        session = await service.get_session(handle)
        await run_inspection_cli(
            session,
            input_stream=input_stream,
            output_stream=output_stream,
            error_stream=error_stream,
        )
    finally:
        primary = sys.exception()
        try:
            await service.close()
        except BaseException as error:
            if primary is not None:
                primary.add_note(f"secondary MemSandbox service close failure: {error}")
            else:
                error.add_note("MemSandbox service close failed after interactive sample")
                raise


def main() -> None:
    """Run the interactive sample from the command line."""
    asyncio.run(run_interactive_sample())


if __name__ == "__main__":
    main()
