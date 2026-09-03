# Tool/Capability Integration Design

**Status:** Proposed detailed design under the approved high-level architecture

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

## Default tool surface

| Tool | Session operation | Visibility |
|---|---|---|
| `execute` | `SandboxSession.execute` | Default |
| `read_file` | `SandboxSession.read_file` | Default |
| `write_file` | `SandboxSession.write_file` | Default |
| `apply_patch` | `SandboxSession.apply_patch` | Default |
| `list_files` | `SandboxSession.list_entries` | Optional |
| `file_info` | Workspace metadata through session | Optional |
| `search_files` | Constrained command or native search operation | Optional |

Lifecycle, policy configuration, secret grants, and snapshots remain host-only unless a
separate capability explicitly exposes them.

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
exit_code
stdout
stderr
duration_ms
output_truncated
```

### `read_file`

Input:

```text
path
start_line?
end_line?
```

Output:

```text
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
path
created
previous_hash
current_hash
bytes_written
workspace_revision
```

### `apply_patch`

Input:

```text
path
patch
expected_hash?
```

Output uses `FileMutationResult`.

Framework schemas may add descriptions and examples, but they must preserve domain
semantics.

## Capability object

A lifecycle-aware framework should receive one adapter object:

```python
class SandboxToolsCapability:
    def __init__(
        self,
        session: SandboxSession,
        *,
        profile: ToolProfile,
        error_mapper: ToolErrorMapper,
    ) -> None:
        ...

    def tools(self) -> Sequence[FrameworkTool]:
        ...

    async def close(self) -> None:
        ...
```

Constructor injection is mandatory. The capability may own framework tool wrappers, but
it does not own a host-managed session unless the host explicitly transfers ownership.

## Capability profiles

Profiles keep advertised functionality honest:

- `FILES_ONLY`: read, write, patch, and optional list/info
- `VIRTUAL_SHELL`: default four tools with constrained command execution
- `READ_ONLY`: read, list, info, and search
- `CUSTOM`: explicitly selected tools

The adapter builds tool descriptions from the active profile and command registry. It
does not advertise absent commands or unsupported filesystem features.

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
| Invalid input or path | Non-retryable tool error unless the model can correct the field |
| File not found or stale hash | Correctable structured tool error |
| Policy or secret denied | Terminal denial; do not encourage bypass attempts |
| Command non-zero exit | Successful tool invocation containing the non-zero result |
| Timeout | Structured timeout result or framework timeout error |
| Dependency/internal failure | Framework execution error with safe correlation ID |

Raw exceptions, stack traces, secrets, and unbounded outputs are not returned to the
model.

## Session lifecycle

Preferred host-managed flow:

```text
create/resume session
  -> bind capability
  -> run agent
  -> unbind/close capability
  -> snapshot if requested
  -> close session
```

If a framework supports per-run dependencies, inject the session there. If it supports
capability startup and cleanup hooks, use them only for adapter resources and correlation,
not for hidden global session creation.

## PydanticAI treatment

The PydanticAI adapter should be a native capability or toolset that:

- receives `SandboxSession` in its constructor or run dependencies
- contributes the selected tool profile
- adds concise instructions about command and path limits
- maps correctable errors into the framework's retry mechanism
- leaves session ownership with the host

PydanticAI's capability is a tool packaging and lifecycle seam, not a generic workspace
provider.

## Security

- Owner, tenant, and session identity come from trusted adapter context.
- The model cannot select another session by passing a handle.
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
- Capability profiles expose only enabled tools.
- Output and error redaction occurs before returning to the framework.
- Host-owned sessions are not closed by adapter cleanup.
- Framework cancellation reaches the session operation.

## Maintenance rule

Changes to the default tools, schemas, error mapping, identity propagation, or ownership
rules require updates to this document and the high-level design.
