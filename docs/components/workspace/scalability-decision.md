# Workspace Scalability Decision

**Status:** Approved Milestone 6 decision; no implementation approved by this document

**Decision date:** 2026-09-09

## Decision

MemSandbox will:

1. **Defer content offload.** No content-provider port, placement policy, provider-linked
   snapshot schema, storage adapter, or offload prototype is approved.
2. **Keep the inline-only `MemoryWorkspace` and current default quotas.** Hosts may lower
   quotas or process density to meet their own budgets, but the project will not raise or
   lower defaults from one uncontrolled reference run.
3. **Prioritize separately scoped internal workspace work** in this order:
   - instrument clone, file-hash, directory-hash, and counter-recomputation phases;
   - design a workspace-owned single-pass initial-tree preparation boundary;
   - design immutable path-copying nodes with cached subtree summaries containing hash,
     logical bytes, and logical node count.
4. **Require a separate approved issue or milestone before implementation.** Milestone 6
   records direction only.

The cached hash and incremental accounting alternatives are approved only as properties
of an immutable path-copying tree. Standalone mutable caches or counters are not
approved because stale derived state would weaken quota, hash, and snapshot correctness.

This decision keeps immutable file-content placement, durable snapshot/session metadata,
host filesystem projection, and remote execution separate. None is approved by the
internal workspace direction.

## Evidence

The
[Milestone 6A reference](../../../benchmarks/results/2026-09-09-windows-development/README.md)
contains 24 cases, 120 measured samples, exact dimensions, correctness checksums, Python
allocation observations, and environment/source fingerprints.

The controlled comparisons show:

| Comparison with `active_project` | Controlled change | Seed | Same-size overwrite | Directory copy | Snapshot encode |
|---|---|---:|---:|---:|---:|
| `node_heavy` | 4x files/directories, same 512 KiB | 15.65x | 4.92x | 3.46x | 1.62x |
| `content_heavy` | 16x bytes, same topology | 3.39x | 3.79x | 2.24x | 22.02x |

At fixed topology, increasing logical content from 512 KiB to 8 MiB raised maximum
retained Python allocation from 535.5 KiB to 8,215.5 KiB and encoded snapshot size from
703.9 KiB to 10,943.9 KiB. At fixed logical content, 4x nodes raised retained allocation
only to 601.1 KiB and encoded snapshot size to 766.8 KiB.

The current implementation explains the operational result:

- each seed file is applied through a separate `MemoryWorkspace.write`;
- every mutation clones the complete committed tree;
- every commit traverses the complete candidate tree, hashes every file and directory,
  and recomputes byte and node counters;
- portable snapshot creation must still enumerate and encode every logical file.

Node growth therefore exposes repeated whole-tree mutation work before resident content
becomes the primary operational constraint. Content bytes remain material for hashing,
live memory, and portable snapshots, but offloading them would not remove tree cloning,
metadata traversal, or directory-hash and counter recomputation.

The reference is non-gating and came from one uncontrolled Windows workstation. It does
not establish production percentiles, process RSS, multi-hour retention behavior, or a
deployment need above the current logical workspace limit.

The current defaults remain 4 MiB per file, 16 MiB total logical workspace bytes,
10,000 logical nodes including root, and 32 MiB per encoded snapshot or archive
boundary. These are correctness ceilings, not claims that every near-limit topology
meets a latency objective.

## Decision criteria

Alternatives are evaluated against:

1. the measured primary constraint;
2. fast seeded and empty provisioning;
3. steady-state mutation latency;
4. resident memory and portable snapshot size;
5. workspace path, quota, revision, hash, atomicity, and restore invariants;
6. compatibility with session, command, event, snapshot-store, service, and policy
   boundaries;
7. implementation and operational complexity;
8. whether current evidence is sufficient to approve the change.

Outcome terms:

- **Retain:** available now as configuration or deployment practice.
- **Approve follow-up:** preferred technical direction, but implementation needs a
  separate approved scope.
- **Defer:** preserve the option and reopen only when the documented trigger is met.
- **Reject:** do not pursue under the current evidence.

## Alternative matrix

| Alternative | Fit to measured constraint | Provisioning and mutation effect | Memory and snapshot effect | Correctness / compatibility risk | Operational cost | Outcome |
|---|---|---|---|---|---|---|
| Preserve or lower host-selected quotas | Bounds worst-case work; does not remove it | Lower limits can cap latency by rejecting larger workspaces | Bounds live and snapshot bytes | Low implementation risk; lower values can reject currently valid workloads | Low | **Retain** as a host safety control; keep project defaults unchanged |
| Raise default quotas | Expands exposure to the measured constraint | Expected to worsen seed and mutation cost | Increases resident and encoded snapshot bytes | Existing limits remain correct, but larger defaults change product safety expectations | Low implementation cost, higher capacity risk | **Reject** without new controlled evidence and an approved host budget |
| Lower process density | Addresses aggregate host memory, not per-workspace algorithms | No improvement to one workspace | Reduces simultaneous aggregate residency | No workspace semantic risk | Ongoing capacity and scheduling cost; RSS evidence is still missing | **Retain** as a deployment mitigation after host measurement, not a library fix |
| Single-pass initial-tree preparation | Directly removes repeated seed clone/remeasure cycles | Expected to improve seeded provisioning; no effect on later mutations | Avoids transient seed work; final live/snapshot size unchanged | Must preserve normalized duplicate rejection, quotas, root hash, final revision, atomic pre-start publication, and cancellation | Medium, internal only | **Approve follow-up** as the first implementation candidate |
| Structural sharing alone | Reduces complete-tree copying | Path copying can make mutation work proportional to affected depth/subtree, but full remeasurement would remain | May reduce transient allocation; final content unchanged | High unless nodes are immutable; copy/move, patch, restore, and logical tree counting must not depend on object identity | Medium-high, internal only | **Approve only as part of the combined immutable-tree design** |
| Cached subtree hashes alone | Targets repeated file and directory hashing | Helps commits only if unchanged subtrees can safely reuse hashes; cloning and counters remain | No material live-content or portable-size reduction | Mutable-node invalidation can silently corrupt root identity | Medium, internal only | **Reject standalone mutable caching; approve immutable cached summaries** |
| Incremental accounting alone | Targets repeated byte/node counting | Removes one part of commit traversal; cloning and hashing remain | No content or snapshot-size reduction | Delta errors can admit over-quota state or reject valid state | Medium, internal only | **Reject standalone counters; approve counters in immutable subtree summaries** |
| Immutable path-copying tree with cached subtree summary | Directly targets clone, file/directory hash, and counter work together | Best fit for ongoing mutation; unchanged subtrees reuse verified summaries | Reduces transient mutation work, not logical content or portable snapshot bytes | High design/test burden, but no new external failure modes; must retain a logical tree and no addressable history | High, internal only | **Approve follow-up** after phase instrumentation |
| Content offload | Targets resident file bytes and could enable provider-linked metadata-only resume | Does not remove measured whole-tree structural work; writes add provider I/O and conflict revalidation | Can reduce resident content and provider-linked snapshot bytes; portable export still materializes content | Crosses workspace, service composition, snapshot schema/retention, accounting, event, and failure boundaries | Very high and provider-operational | **Defer**; evidence does not justify a prototype or provider selection |

## Approved internal direction

### 1. Measure internal phases

A separately scoped benchmark change should measure, without exposing benchmark hooks in
production APIs:

- complete-tree clone;
- file-content hashing;
- directory hashing;
- byte and node accounting;
- path resolution and parent construction;
- snapshot enumeration and encoding.

The result determines whether the combined design should optimize all phases at once or
stage the work. It must retain the public-boundary reference so internal timers cannot
hide total cost.

### 2. Prepare initial state once

The service currently loops over `initial_files` and invokes a complete workspace
mutation for each file before session publication. A future design should translate
service seed contracts into workspace-owned domain values and call one narrow
workspace-owned preparation boundary.

That boundary must:

- normalize and validate all paths before publication;
- reject duplicate normalized paths and file/directory conflicts;
- enforce file, total-byte, node, path, and segment limits over the candidate state;
- calculate the same final root hash and counters;
- preserve the existing final revision semantics without exposing intermediate states;
- publish once while the session is still `CREATED`;
- leave no partial state after invalid input, cancellation, or failure.

The service must not construct workspace-private nodes or duplicate workspace validation.

### 3. Combine path copying, cached hashes, and incremental accounting

If phase evidence confirms the expected split, use immutable nodes. Each node may carry a
verified summary:

```text
subtree hash
logical subtree bytes
logical subtree node count
```

A mutation creates the changed file or subtree and new ancestors to the root. Unchanged
subtrees and their summaries may be reused. Quotas are checked from old/new summaries
before one committed root replacement.

The design must preserve:

- exact SHA-256 file, directory, and root hashes;
- root-inclusive logical node counts even if immutable objects are physically shared;
- one revision increment for each successful logical mutation;
- compare-and-swap preconditions under the workspace lock;
- atomic multi-file patch, copy, move, remove, and restore behavior;
- the session-to-workspace lock order and no await after publication;
- deterministic listing, snapshot, and archive bytes;
- no retained, addressable MVCC history or unbounded obsolete roots.

Copying an immutable subtree may reuse physical objects only if two logical paths cannot
become mutable aliases and quota accounting counts both logical paths. Restore candidates
remain detached, validated, one-use capabilities.

## Boundary compatibility

| Boundary | Existing invariant | Decision impact |
|---|---|---|
| Workspace | Owns paths, content identity, quotas, revisions, hashes, locking, mutation atomicity, and restore validation | Internal representation may change only behind existing focused behavior; new seed preparation must be workspace-owned |
| Sandbox service | Composition root creates one workspace/executor per session and publishes only after complete startup | May call one narrow seed-preparation port; must not manipulate tree internals or publish partially seeded sessions |
| Sandbox session | Consumes focused reader, mutator, and snapshot ports; serializes operations and preserves post-publication completion | No port change is needed for internal optimization; timing and representation stay unobservable |
| Command executor | Consumes focused workspace ports and owns command bounds, cancellation, redirection, and protected persistence | No change; commands must not see cached nodes, content references, or host paths |
| Event sink | Accepts bounded classified metadata; content, provider credentials, host paths, and raw exceptions are excluded | Existing revision/hash/count events remain stable; internal phase data stays benchmark-only |
| Snapshot codec/store | Codec owns deterministic portable bytes and integrity; store owns opaque payload retention, TTL, and byte/count quotas | Internal tree changes must preserve encoded bytes; content offload would require a separate linked-snapshot schema and reachability contract |
| Policy | May deny before collaborators or narrow operation timeout; component hard limits remain authoritative | Internal optimization adds no protected action; future provider calls/accounting would require a focused resource-boundary design, not placement fields in current policy |

Session and command modules already consume focused workspace protocols. The concrete
`MemoryWorkspace` dependency is correctly located in the service composition root. An
internal tree optimization does not require speculative provider interfaces or changes
to public collaborators.

## Explicitly separate decisions

| Concern | Milestone 6 result |
|---|---|
| Immutable file-content placement | **Deferred.** No inline/external union, provider port, placement threshold, or content adapter is approved. |
| Durable snapshot/session metadata | **Not approved here.** Durability requires its own ownership, authorization, retention, recovery, and fencing design. |
| Host filesystem projection | **Not approved.** Virtual `SandboxPath` values must never become arbitrary host paths or mounts. |
| Remote or external execution | **Not approved here.** It remains a separate capability-profile and external-execution decision. |
| Process density | **Host operation only.** It neither changes workspace semantics nor authorizes shared persistence. |

## Content-offload reconsideration

Open a new decision issue only when at least one capacity trigger is demonstrated:

1. A named representative workload cannot complete within the current 16 MiB logical
   workspace limit while individual files remain bounded, and raising the host-selected
   limit would violate an approved memory or process-density budget.
2. After internal mutation work is measured or improved, controlled process RSS shows
   resident file content is the binding constraint against an approved per-session or
   per-process memory budget.
3. A concrete product requirement needs provider-linked metadata-only resume, and
   portable snapshot size or latency violates an approved snapshot budget.

Before approval, all readiness facts must also exist:

- workload size, file-size, access, mutation, lifetime, and snapshot-retention
  distributions;
- target provisioning, mutation, memory, and snapshot budgets on a named controlled
  runner;
- provider namespace, authenticated ownership, durability, retention, garbage
  collection, timeout, and recovery requirements;
- an explicit portable-versus-provider-linked snapshot contract;
- cumulative provider-byte and operation accounting owned by the appropriate resource
  boundary.

The next review point is the earliest of:

- completion of the separately scoped internal mutation optimization and its controlled
  remeasurement;
- a proposal to raise `max_total_bytes`, `max_nodes`, or `max_snapshot_bytes`;
- approval of a durable snapshot backend or metadata-only resume requirement;
- a demonstrated capacity trigger above.

Until then, this decision remains **defer content offload**.

## Maintenance rule

Changes to workspace representation, initial seeding, cached identities, incremental
quota accounting, default limits, process-density assumptions, snapshot modes, or
content placement must update this decision and the workspace README. Content placement
changes must also update the content-offload design, snapshot-store design, and
product-validation evidence.
