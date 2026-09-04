# Workspace/Backend Integration Design

**Status:** OpenAI-first Milestone 5 design; other framework backends deferred

## Purpose

The workspace/backend adapter implements a framework-owned filesystem, workspace,
sandbox client, or live-session contract by delegating to the framework-neutral sandbox
service and session.

This is a deeper integration than ordinary tools. It allows framework-provided file,
shell, lifecycle, or snapshot features to operate on the virtual workspace when their
semantics can be supported honestly.

## When to use

Use this integration only when the framework exposes a stable replaceable contract such
as:

- filesystem backend
- workspace backend
- sandbox client and session
- execution environment
- snapshot or resume state

Current implementation target:

- OpenAI Agents SDK `BaseSandboxClient` and `BaseSandboxSession`

LangChain Deep Agents remains a possible follow-up after the OpenAI adapter proves which
backend behavior and translation helpers are genuinely reusable.

## Architecture

```text
framework runner
  -> framework backend/client contract
  -> adapter
  -> SandboxService lifecycle
  -> SandboxSession operations
  -> core components
```

The model and agent loop still run in their normal framework and provider locations.
Only workspace and command operations are redirected.

## Adapter responsibilities

- Implement the exact framework contract for the supported SDK version.
- Create, attach, resume, and delete core sessions.
- Map framework paths into `SandboxPath`.
- Map framework file and command calls into session requests.
- Convert binary streams and structured results.
- Translate lifecycle and snapshot state.
- Declare or validate supported features.
- Reject incompatible manifests, mounts, users, ports, PTY, or shell semantics.
- Return framework-required wrapper objects and instrumentation hooks.

## Core responsibilities that remain unchanged

- path containment
- file and directory semantics
- command parsing and dispatch
- operation policy
- secret access
- snapshot content
- concurrency and mutation ordering
- domain events

The adapter must not duplicate these rules.

## Lifecycle mapping

| Framework concept | Core mapping |
|---|---|
| Create backend/session | `SandboxService.create` plus session lookup |
| Start | Ensure the core session is running and materialize supported initial state |
| Running/health | Session lifecycle state |
| Stop/persist | Create or update a snapshot when configured |
| Shutdown | Close live session resources |
| Delete | `SandboxService.delete` |
| Resume | `SandboxService.resume` or attach to a still-live owned session |
| Serialize state | Safe provider state plus snapshot reference; no live objects or secrets |

Cleanup must be idempotent because frameworks may invoke stop, shutdown, close, and delete
through separate paths.

## Filesystem mapping

| Framework operation | Session/core operation |
|---|---|
| Read stream | Private full-byte read |
| Write stream | Private full-byte write |
| List | Native list entries |
| Make directory | Native workspace mutation |
| Remove | Native workspace mutation |
| Patch | Session patch operation |
| Extract archive | Bounded safe archive import or explicit unsupported error |
| Persist workspace | Snapshot codec or portable archive adapter |
| Hydrate workspace | Validated restore into a new/exclusive workspace |

The bounded text `read_file` tool remains separate from full binary reads required by
backend contracts.

## Command mapping

Framework contracts may pass:

- raw command text
- argv
- a shell prefix such as `sh -lc`
- working directory
- user identity
- timeout
- PTY requirements

The adapter must choose one of two explicit modes.

### Native virtual-command mode

Translate supported framework calls into the project's constrained command language.
Reject shell wrappers or features that cannot be represented. Pair this mode with custom
framework capabilities or tools.

### Compatibility mode

Implement the framework's expected shell and command behavior. This is a separate,
versioned compatibility profile and must not silently call the host shell.

The first version uses native virtual-command mode.

## Feature declaration

Each adapter documents a support matrix:

```text
binary files
directories
patching
virtual commands
POSIX shell compatibility
users/groups
permissions
symlinks
mounts
ports
PTY
snapshots
preserved live resume
```

Unsupported create-time configuration fails before session startup. Unsupported runtime
operations return the framework's explicit unavailable error.

## Manifest and initial workspace

Framework manifests or initial file trees are translated into a neutral `WorkspaceSeed`.
The adapter validates every entry before mutation.

Version 1 supports:

- directories
- text and binary files
- explicit file metadata that the workspace models

Version 1 rejects:

- host path mounts
- cloud mounts
- users and groups
- devices, sockets, hard links, and symlinks
- executable hooks outside the virtual command registry

The adapter never falls back to materializing rejected entries on the host.

## Saved state

Serialized adapter state may contain:

- adapter type and schema version
- core session or workspace identifier when safe to reattach
- snapshot reference
- supported immutable recreation options
- framework-required fingerprint or readiness metadata

It must not contain:

- live Python objects
- resolved secrets
- host credentials
- untrusted host paths
- event sink or dependency instances

Resume first attempts safe reattachment when the service owns a live session. Otherwise
it creates a replacement from the snapshot.

## Deferred LangChain Deep Agents treatment

If selected after the OpenAI integration, the adapter implements the expected backend
file operations and, when enabled, execution by delegating to one `SandboxSession`.

LangGraph checkpointer state and sandbox snapshots remain separate:

- checkpointer: workflow and conversation state
- sandbox snapshot: workspace and command-environment state

The adapter may place an opaque snapshot reference in workflow state but does not merge
the two formats. Deep Agents does not gate Milestone 5.

## OpenAI Agents SDK treatment

The adapter implements:

- `BaseSandboxClientOptions`
- `SandboxSessionState`
- `BaseSandboxClient`
- `BaseSandboxSession`

The exact pinned contract is documented in
[OpenAI `SandboxAgent` Backend Contract](../../OPENAI_SANDBOX_AGENT_ADAPTER.md).

The abstract session methods are not the model tool interface. OpenAI capabilities call
public session methods, while running and workspace persistence are lifecycle hooks.

Because OpenAI Sandbox Agents are beta, this adapter is isolated, version-pinned, and
covered by SDK contract tests.

## Error mapping

The adapter preserves the difference between:

- normal command non-zero exit
- unsupported backend feature
- invalid path or manifest
- policy denial
- timeout or cancellation
- snapshot incompatibility
- backend/session unavailability
- internal adapter failure

Framework-specific errors retain a safe correlation identifier and stable domain category.

## Conformance scenario

Every workspace/backend adapter must:

1. Create a session from the same neutral seed.
2. List and read initial files.
3. Write a binary and a text file.
4. Execute one supported virtual command.
5. Apply a patch.
6. Persist and delete the live session.
7. Resume from saved state.
8. Verify identical workspace hashes and command environment.
9. Reject one unsupported feature before mutation.

## Test expectations

- Contract method signatures match the pinned framework version.
- Lifecycle calls are idempotent and map to one owned core session.
- Binary streams round-trip without text conversion.
- Paths remain POSIX and confined on every host OS.
- Feature validation occurs before startup or mutation.
- Snapshot state round-trips through framework serialization.
- Framework cancellation and timeout reach the core.
- No operation reaches host filesystem or shell as a fallback.
- SDK upgrade tests detect newly abstract methods or changed semantics.

## Maintenance rule

Each adapter pins its supported SDK range. Framework contract, lifecycle, feature, or
serialization changes require updates to this document, the adapter-specific document,
and conformance tests.
