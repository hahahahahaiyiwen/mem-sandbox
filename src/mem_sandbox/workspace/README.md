# Workspace module

The `workspace` package owns the deterministic, host-independent virtual filesystem used
by MemSandbox sessions.

## Boundary

- `SandboxPath` owns `/workspace` confinement and POSIX normalization.
- Immutable request and result models carry bytes, hashes, revisions, and explicit write
  preconditions.
- `MemoryWorkspace` owns committed tree state, quotas, hashes, and the coarse async state
  lock.
- The patch module owns the constrained unified-diff grammar.
- The snapshot codec owns canonical JSON/base64 serialization and integrity validation.

The module never accesses the host filesystem and has no agent-SDK dependency.

## Consistency

One method call is the transaction boundary. Every operation is linearizable under one
workspace state lock. Mutations build private candidate state and publish it once after
all validation succeeds. There are no node-level locks, MVCC versions, or automatic
optimistic retries.

Writes and appends require an explicit precondition: unconditional,
path-must-not-exist, or content-hash-must-equal. Hash comparison happens inside the
state lock. Append is a single workspace-owned mutation that checks quotas against the
complete resulting file and publishes content, hashes, counters, and one new revision
atomically.

## Maintenance

Keep paths host-independent, preserve exact failure atomicity, and update
`docs/components/workspace/README.md` whenever path rules, quotas, hashes, patch syntax,
snapshot encoding, or consistency guarantees change.
