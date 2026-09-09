# Shared Sample Utilities

## Purpose and boundary

This package owns sample utilities that depend only on MemSandbox and Python standard
library contracts. They can be reused by future agent SDK integrations without
importing the OpenAI Agents SDK.

The package currently provides:

- a constrained interactive CLI over a host-owned `SandboxSession`; and
- a process-local MemSandbox service composition root with snapshot dependencies.

Run the standalone credential-free sample with:

```console
uv run python -m samples.shared
```

It creates an empty in-memory session, accepts only the constrained MemSandbox command
language, and closes all process-local state when the CLI exits.

It does not own agent construction, model calls, SDK manifests, provider credentials,
scenario orchestration, or inference-client lifecycle.

## Design invariants

- Inspection calls `SandboxSession.execute` directly and never invokes a host shell.
- The service composition uses only in-memory implementations and explicit dependency
  construction.
- The application that creates a service remains responsible for closing it.
- Shared utilities must not import `agents`, `openai`, or any future framework SDK.

## Maintenance

Move behavior here only after it is genuinely independent of a specific agent SDK.
Framework-shaped runners, manifests, and model contracts remain under their owning SDK
sample package.
