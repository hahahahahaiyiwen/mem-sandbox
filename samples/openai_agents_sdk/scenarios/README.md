# OpenAI Agents SDK Scenario Definitions

## Boundary

Scenario modules own task-specific manifests, prompts, stage structure, policy profiles,
limits, and exact artifact expectations. They do not construct inference-provider
clients, create or delete SDK sessions, implement inspection, or duplicate cleanup.

The shared runner owns:

- one injected Agents SDK `Model`;
- SDK session creation and capability binding;
- bounded sequential agent stages;
- snapshot-fork lifecycle for the branching scenario;
- host-side artifact verification;
- success/failure inspection callbacks;
- cleanup of every SDK session and backend handle.

## Scenario catalog

| Task | Scenario shape | Verified result |
|---|---|---|
| Minimal file-tool smoke test | `workspace-edit`, single stage | One guarded status-file edit |
| Guarded document revision and review | `document-review`, editor then reviewer | Preserved inputs, corrected draft, and evidence report |
| Search supplied logs | `incident-triage`, single stage | Incident report |
| Atomic configuration migration | `config-migration`, single stage | Two configs and migration report |
| Deterministic data transformation | `data-pipeline`, single stage | Event-count artifact |
| Policy-denial recovery | `policy-recovery`, single stage | Evidence-preservation report |
| Quota recovery | `quota-recovery`, single stage | Compact status artifact |
| Sequential role handoff | `multi-agent-handoff`, three stages | Plan, changed config, and review |
| Isolated alternatives | `snapshot-branching`, baseline plus forks | Selected branch from a shared snapshot |

Policy and quota failures must be produced by real MemSandbox boundaries, not described
only in prompts. Snapshot branching must create distinct backend handles. Every verifier
reads through the public `SandboxSession` interface and compares exact bytes.

## `document-review` contract

The flagship scenario is data-driven and uses the ordinary `StagedScenario` runner. It
does not add a document-specific core or adapter API.

| Workspace path | Ownership and invariant |
|---|---|
| `/workspace/source/release-brief.txt` | Host-seeded source of truth; preserved byte-for-byte |
| `/workspace/drafts/release-notes.md` | Host-seeded stale draft; only instructed fields may change |
| `/workspace/review/instructions.md` | Host-seeded edit and review constraints; preserved byte-for-byte |
| `/workspace/review/findings.md` | Reviewer-created Markdown status and path-linked evidence table |

The editor prompt supplies the task, paths, and mutation rules without supplying the
facts it must discover. The editor reads the draft hash immediately before applying one
guarded patch. The reviewer is a distinct `SandboxAgent` run that reads workspace
artifacts independently; it does not receive or trust the editor's final response.

The scenario policy denies command execution and rejects direct `write_file` mutations
outside the review artifact. The current policy request does not expose individual
`apply_patch` target paths, so the prompt limits patch scope while the host verifier
enforces the final boundary: source and instruction bytes must be unchanged, the
protected draft section must remain present in the exact corrected draft, and the
review artifact must match the required evidence structure.

Deterministic tests cover the successful two-stage workflow, a rejected protected write,
a stale draft hash, reviewer dependency failure, and backend cleanup. Live provider runs
are optional and separate from this network-free verification.

## Maintenance

Prefer data-driven `StagedScenario` definitions. Add specialized runner behavior only
when lifecycle semantics differ, as with `SnapshotBranchingScenario`. Do not add tasks
that imply arbitrary Python, Git, network, or host-shell execution.
