# Tool/Capability Integration Design

**Status:** OpenAI profile 1 implemented; other framework capabilities deferred

## Purpose

The tool/capability adapter exposes selected `SandboxSession` operations as typed
model-visible tools. It is the universal integration level because server-side agent
frameworks generally accept Python functions even when they do not define a workspace
backend.

## When to use

Use this integration when a framework provides:

- typed function tools
- toolsets or capability packages
- run dependencies or constructor injection
- optional tool lifecycle hooks

Examples include PydanticAI, Microsoft Agent Framework, Google ADK, Strands, LlamaIndex,
CrewAI, Haystack, Agno, and smolagents.

The first implementation is an OpenAI Agents SDK custom `Capability`. Do not implement a
framework-neutral `FrameworkTool` layer before that adapter exists. Reuse the core request
and result contracts directly, and extract a shared helper only after a second framework
adapter demonstrates identical behavior.

## Architecture

```text
host
  -> creates or resumes SandboxSession
  -> constructs framework capability with that session

model
  -> framework tool call
  -> adapter validates framework payload
  -> adapter builds domain request
  -> SandboxSession operation
  -> adapter formats domain result or error
```

The adapter never finds the session through global mutable state.

## OpenAI profile 1 tool surface

| Tool | Session operation | Visibility |
|---|---|---|
| `execute` | `SandboxSession.execute` | Exposed |
| `read_file` | `SandboxSession.read_file` | Exposed |
| `write_file` | `SandboxSession.write_file` | Exposed |
| `apply_patch` | `SandboxSession.apply_patch` | Exposed |

Lifecycle, policy configuration, secret grants, and snapshots remain host-only unless a
separate capability explicitly exposes them. Profile 1 has no optional tools and does not
compose with the SDK's default shell, filesystem, or compaction capabilities.

## Tool schemas

### `execute`

Input:

```text
command
timeout_seconds?
max_output_bytes?
```

Output:

```text
metadata:
  session_id
  operation_id
  workspace_revision
  started_at
  completed_at
exit_code
failure_code
stdout
stderr
stdout_original_bytes
stderr_original_bytes
stdout_truncated
stderr_truncated
duration_ms
resulting_cwd
environment_changes[]
```

`command` is parsed only by the constrained MemSandbox command executor. The adapter
does not wrap it in `sh -lc` or invoke a host shell. `max_output_bytes` applies
independently to stdout and stderr. `timeout_seconds` is forwarded to both the command
limit and the end-to-end operation deadline.

### `read_file`

Input:

```text
path
start_line?
end_line?
```

Output:

```text
metadata
path
content
start_line
end_line
total_lines
content_hash
```

### `write_file`

Input:

```text
path
content
write_condition
expected_hash?
create_parents?
```

`write_condition` is one of `any_current_state`, `path_must_not_exist`, or
`content_hash_must_equal`. `expected_hash` is required only for
`content_hash_must_equal`; adapters should not infer unconditional overwrite from an
omitted hash.

Output:

```text
metadata
path
created
changed
previous_hash
current_hash
```

### `apply_patch`

Input:

```text
patch
expected_hashes[]:
  path
  content_hash
```

The patch may update multiple files atomically. The adapter passes the patch text and
all expected hashes directly to `ApplyPatchRequest`; it does not parse or rewrite the
diff.

Output:

```text
metadata
files[]:
  path
  previous_hash
  current_hash
```

Every successful result is returned as `{"ok": true, "result": ...}` and preserves the
domain result metadata. Expected failures are returned as
`{"ok": false, "error": ...}` with stable category, code, message, correction guidance,
retry guidance, and an operation ID when one exists.

Framework schemas may add descriptions and examples, but they must preserve domain
semantics.

## First capability object

The first implementation follows the OpenAI SDK's capability lifecycle:

```python
from typing import Literal

from agents.sandbox.capabilities import Capability
from agents.tool import Tool


class InMemorySandboxCapability(Capability):
    type: Literal["mem_sandbox"] = "mem_sandbox"

    def tools(self) -> list[Tool]: ...
```

The host injects the sandbox client or live session through `SandboxRunConfig`. The
OpenAI runner then clones and binds the capability through the SDK-required `bind` hook.
This framework-owned hook is the only exception to the project's constructor-injection
default; the capability never performs global lookup and the model cannot provide a
session handle.

The capability owns framework tool wrappers but not the underlying core service, handle,
or session. It accepts either the provider session or the OpenAI SDK's instrumented
wrapper around that provider session, verifies the concrete provider identity, and uses a
read-only provider-to-domain session seam. Future framework adapters own their native
wrapper types rather than implementing a shared `FrameworkTool` abstraction.

Profile 1 rejects a non-empty `SandboxRunConfig.cwd`. Relative paths therefore continue
to use the core session cwd consistently across file tools, patch headers, expected
hashes, and constrained commands. Supporting a separate model-facing cwd would require a
new profile with explicit command and patch-path translation semantics.

## Capability profiles

The implemented OpenAI profile is intentionally fixed to the four tools above. Additional
profiles such as files-only, read-only, or custom subsets remain future work and require
their own explicit schemas and conformance coverage. The adapter does not advertise
absent commands or unsupported filesystem features.

## Request translation

For each call, the adapter:

1. Lets the framework validate its typed input.
2. Converts paths and limits into domain value objects.
3. Adds trusted run identity from adapter context, not model input.
4. Calls exactly one session operation.
5. Converts the domain result into structured framework output.

The adapter does not parse shell syntax, apply patches, enforce quotas, or resolve
secrets.

## Error translation

Domain categories map to stable tool outcomes:

| Domain category | Tool behavior |
|---|---|
| Invalid input or path | Correctable structured tool error |
| File not found or stale hash | Correctable structured tool error |
| Policy or secret denied | Terminal denial; do not encourage bypass attempts |
| Command non-zero exit | Successful tool invocation containing the non-zero result |
| Timeout | Correctable, retryable structured tool error |
| Dependency/internal failure | Redacted structured error with a safe correlation ID |
| Cancellation | Propagated to the runner; never converted to a model-visible result |

Raw exceptions, stack traces, secrets, and unbounded outputs are not returned to the
model.

## Session lifecycle

Preferred host-managed flow:

```text
create/resume session
  -> bind capability
  -> run agent
  -> discard run-scoped capability clone
  -> snapshot if requested
  -> close session
```

If a framework supports per-run dependencies, inject the session there. If it supports
capability startup and cleanup hooks, use them only for adapter resources and correlation,
not for hidden global session creation.

## OpenAI Agents SDK treatment

The OpenAI adapter defines a custom sandbox `Capability` that:

- is cloned and bound by the SDK to the live in-memory sandbox session;
- contributes exactly `execute`, `read_file`, `write_file`, and `apply_patch`;
- replaces the default shell/filesystem capability set for the first supported profile;
- calls explicit adapter operations backed by one core `SandboxSession`;
- maps correctable inputs and stale hashes into safe tool errors without encouraging
  retries for policy or secret denials;
- leaves client/session creation, snapshot, resume, and deletion with the host and OpenAI
  sandbox lifecycle.

The first profile does not advertise `sh -lc`, PTY, arbitrary shell, image viewing, or
other built-in capability behavior whose complete semantics the in-memory backend does
not implement.

## Deferred PydanticAI treatment

If selected after the OpenAI integration, the PydanticAI adapter should be a native
capability or toolset that:

- receives `SandboxSession` in its constructor or run dependencies
- contributes the selected tool profile
- adds concise instructions about command and path limits
- maps correctable errors into the framework's retry mechanism
- leaves session ownership with the host

PydanticAI's capability is a tool packaging and lifecycle seam, not a generic workspace
provider. It does not gate Milestone 5.

## Security

- Owner, tenant, and session identity come from trusted adapter context.
- The model cannot select another session by passing a handle.
- Model-provided execute timeout and output limits may only narrow fixed capability
  ceilings; they cannot expand host-approved resource use.
- Tool descriptions state the logical-sandbox limitation.
- Model-provided paths and commands always pass through the session and policy engine.
- The adapter never invokes `open`, `os`, `subprocess`, or host shell APIs for sandbox
  behavior.

## Conformance scenario

Every tool adapter must pass the same scenario:

1. Start with an empty session.
2. Create two files.
3. Read a bounded range.
4. Execute a virtual command that inspects the files.
5. Apply a hash-guarded patch.
6. Trigger a stale-hash error.
7. Trigger a policy denial.
8. Snapshot and restore through the host.
9. Confirm identical final workspace state.

## Test expectations

- Tool schemas match domain request boundaries.
- Each tool calls exactly one intended session method.
- Trusted identity cannot be overridden by model input.
- Correctable, denied, timeout, and internal errors map distinctly.
- The fixed first profile exposes exactly four tools and replaces SDK defaults.
- Output and error redaction occurs before returning to the framework.
- Host-owned sessions are not closed by adapter cleanup.
- Framework cancellation reaches the session operation.

## Maintenance rule

Changes to the default tools, schemas, error mapping, identity propagation, or ownership
rules require updates to this document and the high-level design.
