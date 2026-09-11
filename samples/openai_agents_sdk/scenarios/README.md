# OpenAI Agents SDK Scenario Definitions

## Boundary

Scenario modules own task-specific manifests, prompts, stage structure, policy profiles,
limits, and exact artifact expectations. They do not construct inference-provider
clients, create or delete SDK sessions, implement inspection, or duplicate cleanup.

The shared runner owns:

- one injected Agents SDK `Model`;
- SDK session creation and capability binding;
- bounded sequential agent stages;
- snapshot-fork and pause/continue lifecycle, complete branch collection, and host
  selection;
- host-side artifact verification;
- success/failure inspection callbacks;
- cleanup of every SDK session and backend handle.

## Scenario catalog

| Task | Scenario shape | Verified result |
|---|---|---|
| Minimal file-tool smoke test | `workspace-edit`, single stage | One guarded status-file edit |
| Guarded document revision and review | `document-review`, editor then reviewer | Preserved inputs, corrected draft, and evidence report |
| Independent review perspectives | `independent-reviewers`, baseline plus reviewer forks | Unchanged baseline, isolated review results, and one host-selected fork |
| Pause and continue work | `pause-continue`, initial run then replacement resume | JSON-safe state, restored checkpoint, and final result |
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

## `independent-reviewers` contract

The host creates one baseline containing a deployment proposal, review criteria, and an
unreviewed status. After a coordinator reads those files, the runner persists the
baseline once, deletes its live backend, and resumes the same saved state into separate
risk and clarity reviewer sessions.

Both reviewers update `/workspace/review/status.txt` and create
`/workspace/review/findings.md`, but those paths belong to distinct workspace forks.
The host verifies every branch's protected inputs, status, and findings; exposes every
verified branch through `ScenarioResult.branch_results`; and uses the scenario's
`selected_branch` as an explicit host decision. Only the selected branch populates
`ScenarioResult.artifacts` and remains available to `--inspect`.

When `baseline_expected_artifacts` is configured, the runner performs one final resume
from the original saved state after all reviewers finish. Exact verification of its
unchanged status proves branch mutations did not alter the persisted baseline. Distinct
backend handles and exact sibling outputs prove branch isolation. No branch is merged,
and no model conversation or final prose is used as cross-branch state.

The existing `snapshot-branching` scenario remains the smaller generic alternative
example. `multi-agent-handoff` is intentionally different: its planner, implementer, and
reviewer run sequentially in one shared session.

## `pause-continue` contract

The initial agent discovers a release request, patches the workflow status, and writes a
self-contained checkpoint. The host verifies the source, paused status, and checkpoint
bytes before any state is published. It then resumes while the original backend is
available and asserts that this live reattachment uses the same handle and workspace.

The host explicitly calls `stop()` to persist the workspace, serializes
`InMemorySandboxSessionState` to JSON, deserializes it through the adapter, and deletes
the original backend. The next `resume()` must therefore allocate a distinct handle and
restore the retained snapshot. The runner verifies the checkpoint bytes immediately
after restoration and before starting the continuation task.

The continuation is a new `SandboxAgent` and `Runner.run` call. It receives no previous
conversation or model final response; its prompt directs it to read the checkpoint as
the only cross-run task context. Exact final verification proves that the source and
checkpoint were preserved and that the resumed status and result were produced.

Missing snapshots fail before replacement allocation. Continuation failures remain
inspectable against the restored workspace, and cleanup deletes every source alias and
replacement handle. Snapshot-store construction, state custody, retention, and service
lifetime remain host responsibilities. The sample store is process-local and demonstrates
same-process continuation, not recovery after process loss.

## Maintenance

Prefer data-driven `StagedScenario` definitions. Add specialized runner behavior only
when lifecycle semantics differ, as with `SnapshotBranchingScenario` and
`PauseContinueScenario`. Do not add tasks that imply arbitrary Python, Git, network, or
host-shell execution.
