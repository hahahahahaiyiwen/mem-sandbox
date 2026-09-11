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

## Conformance matrix

The matrix is an inventory of maintained evidence, not a requirement to repeat
the same adapter invariant in every scenario. `Shared` means the scenario uses
the same runner or capability path as the cited test. `N/A` means the scenario
does not claim that behavior; it must not be replaced with a prose-only simulated
failure.

Evidence keys:

- **ALL** -
  `test_registered_scenario_runs_without_network_and_cleans_backends` runs every
  registry entry with the deterministic model, checks test-owned expected
  artifact paths and bytes, stage order, exact four-tool exposure, applicable
  branch selection and identity, and backend cleanup.
- **STAGED-FAIL / STAGED-CANCEL** -
  `test_inspect_on_failure_runs_before_cleanup_and_preserves_error` and
  `test_failure_does_not_inspect_without_requested_option` exercise dependency
  failure and cancellation through the shared `StagedScenario` path.
- **SDK-BOUNDS** -
  `tests/integrations/openai_agents/test_capability.py` tests
  `test_execute_accepts_fixed_profile_limit_boundaries`,
  `test_execute_rejects_limits_above_fixed_profile_ceiling`, and
  `test_expected_domain_errors_are_structured_and_cancellation_propagates` cover
  shared adapter bounds, structured failures, and cancellation propagation.
- **CORE-STALE** -
  `tests/unit/workspace/test_patching.py::test_stale_hash_rejects_patch_without_mutation`
  proves a rejected guarded patch leaves workspace state unchanged.
- **DOC-GUARDS / DOC-FAIL** - document-review tests prove exact output, real
  `session_policy_denied` and `stale_content` results without protected-file
  mutation, reviewer ordering, dependency failure preservation, and cleanup.
- **FORK / FORK-FAIL / SNAP-CANCEL** - independent-reviewer and snapshot tests
  prove distinct handles, unchanged baselines, sibling isolation, host selection,
  branch dependency failure, cancellation, and cleanup.
- **PAUSE / PAUSE-INVALID / PAUSE-FAIL / PAUSE-CANCEL** - pause/continue tests
  prove same-handle live attachment, JSON-safe state, distinct replacement
  identity, a fresh conversation, invalid or missing state rejection, dependency
  failure or cancellation boundaries, and cleanup.
- **POLICY / QUOTA** - recovery tests retain the actual failed tool result and
  assert its structured denial or quota code before verifying the narrower
  recovery artifact.
- **HANDOFF-FAIL** - the multi-agent failure test proves completed planner state
  remains inspectable when the implementer dependency fails, then cleans up.
- **PROVIDER** -
  `test_provider_application_runs_without_network_and_closes_client`,
  `test_provider_application_reports_pause_continue_lifecycle`, and both
  provider `--list-scenarios` tests cover client/service ownership and
  configuration-free discovery without live calls.

| Scenario | Exact success | Policy / stale rejection | Dependency failure | Bounds | Cancellation | Interaction / identity | Cleanup |
|---|---|---|---|---|---|---|---|
| `workspace-edit` | ALL | N/A - no rejection contract | STAGED-FAIL | SDK-BOUNDS | STAGED-CANCEL | ALL: one stage, one handle | ALL, STAGED-FAIL, STAGED-CANCEL |
| `document-review` | DOC-GUARDS | DOC-GUARDS | DOC-FAIL | SDK-BOUNDS | Shared STAGED-CANCEL | DOC-GUARDS and ALL: editor before reviewer, one handle | DOC-GUARDS, DOC-FAIL, Shared STAGED-CANCEL |
| `independent-reviewers` | FORK | N/A - isolation, not rejection | FORK-FAIL | SDK-BOUNDS | Shared SNAP-CANCEL | FORK: baseline plus distinct risk and clarity handles | FORK, FORK-FAIL, Shared SNAP-CANCEL |
| `pause-continue` | PAUSE | PAUSE-INVALID | PAUSE-FAIL | SDK-BOUNDS | PAUSE-CANCEL | PAUSE: live alias plus fresh replacement run | PAUSE, PAUSE-INVALID, PAUSE-FAIL, PAUSE-CANCEL |
| `incident-triage` | ALL | N/A - no rejection contract | Shared STAGED-FAIL | SDK-BOUNDS | Shared STAGED-CANCEL | ALL: execute before report write | ALL, Shared STAGED-FAIL, Shared STAGED-CANCEL |
| `config-migration` | ALL | Shared DOC-GUARDS and CORE-STALE | Shared STAGED-FAIL | SDK-BOUNDS | Shared STAGED-CANCEL | ALL: two reads before one guarded atomic patch | ALL, Shared STAGED-FAIL, Shared STAGED-CANCEL |
| `data-pipeline` | ALL | N/A - no rejection contract | Shared STAGED-FAIL | SDK-BOUNDS | Shared STAGED-CANCEL | ALL: execute before host-verified read | ALL, Shared STAGED-FAIL, Shared STAGED-CANCEL |
| `policy-recovery` | ALL, POLICY | POLICY | Shared STAGED-FAIL | SDK-BOUNDS | Shared STAGED-CANCEL | POLICY: denied execute before file-tool recovery | ALL, POLICY, Shared STAGED-CANCEL |
| `quota-recovery` | ALL, QUOTA | N/A - quota is the applicable bound | Shared STAGED-FAIL | QUOTA, SDK-BOUNDS | Shared STAGED-CANCEL | QUOTA: rejected oversized write before bounded retry | ALL, QUOTA, Shared STAGED-CANCEL |
| `multi-agent-handoff` | ALL | N/A - no rejection contract | HANDOFF-FAIL | SDK-BOUNDS | Shared STAGED-CANCEL | ALL: planner, implementer, reviewer share one handle | ALL, HANDOFF-FAIL, Shared STAGED-CANCEL |
| `snapshot-branching` | ALL, FORK | N/A - isolation, not rejection | FORK-FAIL | SDK-BOUNDS | SNAP-CANCEL | FORK: distinct conservative and aggressive handles | ALL, FORK-FAIL, SNAP-CANCEL |

`PROVIDER` applies to the registry and application lifecycle as a whole rather
than to one scenario row.

When a scenario changes, update its row and deterministic evidence in the same
change. Add scenario-owned negative coverage only when the scenario promises
state-preservation or lifecycle semantics beyond the shared runner; otherwise
reference the shared invariant. Every new lifecycle runner needs success,
dependency-failure, cancellation, identity, and cleanup evidence before it is
registered.

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
