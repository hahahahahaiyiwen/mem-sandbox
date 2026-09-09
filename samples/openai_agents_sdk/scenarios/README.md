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

## Scenario categories

- **Single-stage:** workspace editing, incident triage, configuration migration, data
  pipeline, policy recovery, and quota recovery.
- **Multi-stage:** planner, implementer, and reviewer agents share one host-selected
  session.
- **Snapshot branching:** a baseline stage is persisted, two isolated sessions resume
  from the same state, and the configured selected branch remains available for
  inspection.

Policy and quota failures must be produced by real MemSandbox boundaries, not described
only in prompts. Snapshot branching must create distinct backend handles. Every verifier
reads through the public `SandboxSession` interface and compares exact bytes.

## Maintenance

Prefer data-driven `StagedScenario` definitions. Add specialized runner behavior only
when lifecycle semantics differ, as with `SnapshotBranchingScenario`. Do not add tasks
that imply arbitrary Python, Git, network, or host-shell execution.
