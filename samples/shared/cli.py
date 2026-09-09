"""SDK-independent inspection CLI over one live MemSandbox session."""

from __future__ import annotations

import sys
from typing import TextIO

from mem_sandbox.session import SandboxSession, SessionExecuteRequest

_HELP = """Supported commands:
  pwd, cd, ls, cat, echo, mkdir, touch, rm
  head, tail, grep, find, wc, sort, uniq, cp, mv
  env, export, unset
Supported expressions include pipes, ';', '&&', and output redirection.
This is the constrained MemSandbox command language, not a host shell.
Type 'exit' or 'quit' to close the sandbox.
"""


async def run_inspection_cli(
    session: SandboxSession,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
    error_stream: TextIO | None = None,
) -> None:
    """Run commands against the supplied live session until exit or EOF."""
    selected_input = sys.stdin if input_stream is None else input_stream
    selected_output = sys.stdout if output_stream is None else output_stream
    selected_error = sys.stderr if error_stream is None else error_stream

    selected_output.write(
        "MemSandbox inspection CLI\n"
        "Commands run in the constrained in-memory environment.\n"
        "Type 'help' for commands or 'exit' to close the sandbox.\n"
    )
    selected_output.flush()

    while True:
        selected_output.write(f"mem-sandbox:{session.cwd.value}> ")
        selected_output.flush()
        line = selected_input.readline()
        if line == "":
            selected_output.write("\n")
            selected_output.flush()
            return

        command = line.strip()
        if not command:
            continue
        if command.casefold() in {"exit", "quit"}:
            return
        if command.casefold() in {"help", "?"}:
            selected_output.write(_HELP)
            selected_output.flush()
            continue

        result = await session.execute(SessionExecuteRequest(command=command))
        if result.stdout:
            selected_output.write(result.stdout)
            selected_output.flush()
        if result.stderr:
            selected_error.write(result.stderr)
            selected_error.flush()
        if result.exit_code != 0:
            failure = "unknown" if result.failure_code is None else result.failure_code.value
            selected_error.write(f"[exit {result.exit_code}; {failure}]\n")
            selected_error.flush()
