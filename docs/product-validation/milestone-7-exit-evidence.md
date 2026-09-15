# Milestone 7 Exit Evidence

This index reconciles the workspace-first showcase and integration-usability
milestone for [issue #78][issue-78] and [tracker #70][tracker]. It covers every
completed `7.1`-`7.3` item and all four exit criteria in the
[implementation plan][plan], without adding another capability or changing the
maintained scenarios.

The delivery baseline is `6246439835d53c59ad2dca7db0be21388a10ce9d`. Historical
child results and retained live reports describe their own source revisions.
The [closure validation record][closure-record] identifies the exact pushed
revision, environment, commands, outcomes, and outstanding gates for #78.
Historical success is not a substitute for that fresh validation.

## Exit criteria

| Criterion | Concrete evidence | Interpretation and limits |
|---|---|---|
| **7.9.1**: runnable, host-verified, documented workflows with compatible names and behavior | The [scenario contracts and conformance matrix][scenario-guide], [runner][runner], and [deterministic sample tests][scenario-tests] cover `document-review`, `independent-reviewers`, and `pause-continue`. Both [OpenAI][openai-guide] and [Azure OpenAI][azure-guide] expose the same registry. | The host reads and compares exact artifact bytes; model prose is not acceptance. All 11 scenario names remain available, with `workspace-edit` still the default. A live provider can fail even when deterministic conformance passes. |
| **7.9.2**: deterministic, network-free correctness evidence separated from live requirements and costs | The [conformance matrix][scenario-guide] links success, rejection, dependency-failure, bounds, cancellation, identity, and cleanup tests. The [evaluation contract][evaluation-guide] and [retained live result set][live-results] record environment, commands, versions, timings, usage, and limitations. | Model doubles and host-owned fakes provide required behavior evidence without inference calls. Dependency/package acquisition is a separate host setup operation. Live observations are non-deterministic and non-gating; unavailable monetary cost is not zero. |
| **7.9.3**: reproducible installed-package and supported SDK composition | The [adapter guide][adapter-guide], [distribution tests][distribution-tests], [isolated installed-package probe][installed-probe], [SDK contract tests][sdk-tests], and [host-tool composition tests][composition-tests] verify the public usage path. | Declared SDK support is not the same as individually exercised versions. Samples are repository-only. Application tool authority stays outside the four-tool MemSandbox capability; default shell/filesystem capabilities are not added. |
| **7.9.4**: current capabilities, optional extensions, and the next-priority decision are distinct | The [public positioning][root-guide], [product-validation guidance][validation-guide], and [workflow-blocker assessment][assessment] preserve #77's decision and review triggers. | The selected disposition is to defer new capability and second-SDK work. No networking, arbitrary execution, durable infrastructure, automatic fork merging, or conversation persistence is delivered by this milestone. |

## Verified source delivery

On 2026-09-15, all nine source issues below were verified closed with
`state_reason=completed`, with matching issue/PR closing references. Every PR
merged into `main`; its merge commit is reachable from the delivery baseline.
All four historical exact-head Quality jobs passed for each PR (Python
3.12/3.14 on Linux/Windows); the table links one retained job per delivery.
These are historical results, not #78's fresh validation.

| Source issue | Merged PR | Merge commit | Retained CI |
|---|---|---|---|
| [#66][issue-66] | [#68][pr-68] | [`08962b8`](https://github.com/hahahahahaiyiwen/mem-sandbox/commit/08962b84750c14f39acce7f6bb32eb899c8a3510) | [Quality job](https://github.com/hahahahahaiyiwen/mem-sandbox/actions/runs/34513113889/job/102991859460) |
| [#67][issue-67] | [#69][pr-69] | [`db1e35e`](https://github.com/hahahahahaiyiwen/mem-sandbox/commit/db1e35ea7e26f1e39cc5b45c6e55260611038127) | [Quality job](https://github.com/hahahahahaiyiwen/mem-sandbox/actions/runs/34529521046/job/103046555334) |
| [#71][issue-71] | [#82][pr-82] | [`208e762`](https://github.com/hahahahahaiyiwen/mem-sandbox/commit/208e762a123f39b1d9bf3d3a19bdb6549eea4edf) | [Quality job](https://github.com/hahahahahaiyiwen/mem-sandbox/actions/runs/34633859248/job/103376924275) |
| [#72][issue-72] | [#80][pr-80] | [`ab14890`](https://github.com/hahahahahaiyiwen/mem-sandbox/commit/ab148902505efdaa9d1c04f8214652ae086469a5) | [Quality job](https://github.com/hahahahahaiyiwen/mem-sandbox/actions/runs/34545570054/job/103097318281) |
| [#73][issue-73] | [#81][pr-81] | [`a615012`](https://github.com/hahahahahaiyiwen/mem-sandbox/commit/a6150124ead6a81b54e0c24db7adc44374c49b73) | [Quality job](https://github.com/hahahahahaiyiwen/mem-sandbox/actions/runs/34628851930/job/103360471329) |
| [#74][issue-74] | [#79][pr-79] | [`aa50264`](https://github.com/hahahahahaiyiwen/mem-sandbox/commit/aa502649df7840d88d9dc3e34e427fb10ae297e1) | [Quality job](https://github.com/hahahahahaiyiwen/mem-sandbox/actions/runs/34541638896/job/103085375596) |
| [#75][issue-75] | [#83][pr-83] | [`7b6c6a8`](https://github.com/hahahahahaiyiwen/mem-sandbox/commit/7b6c6a81e183d08ef2e25f16413b9e24fb6e15aa) | [Quality job](https://github.com/hahahahahaiyiwen/mem-sandbox/actions/runs/34646533036/job/103418539460) |
| [#76][issue-76] | [#84][pr-84] | [`dd82c7a`](https://github.com/hahahahahaiyiwen/mem-sandbox/commit/dd82c7a5e006e014ce3b930edd6b19d392d9f8a3) | [Quality job](https://github.com/hahahahahaiyiwen/mem-sandbox/actions/runs/34775675400/job/103773226996) |
| [#77][issue-77] | [#87][pr-87] | [`6246439`](https://github.com/hahahahahaiyiwen/mem-sandbox/commit/6246439835d53c59ad2dca7db0be21388a10ce9d) | [Quality job](https://github.com/hahahahahaiyiwen/mem-sandbox/actions/runs/34787892263/job/103806603711) |

## Work-item evidence

The source issues own their delivery decisions and merged-PR evidence. The
following mapping keeps completed baseline work (#66 and #67) separate from the
focused Milestone 7 children.

| PLAN item | Source | Implemented outcome or decision | Tests, documentation, commands, or retained report |
|---|---|---|---|
| **7.1.1** | [#66][issue-66] | Guarded document correction followed by independent review and exact host verification. | [Document scenario][document-scenario], [sample tests][scenario-tests], and [reader workflow][sample-guide]; use the deterministic workflow command below. |
| **7.1.2** | [#74][issue-74] | Distinct reviewer forks from one immutable baseline; host verifies both branches and selects one without merging. | [Reviewer scenario][reviewer-scenario], `test_independent_reviewers_preserve_baseline_and_isolate_sibling_results` in the [sample tests][scenario-tests], and the [fork contract][scenario-guide]. |
| **7.1.3** | [#72][issue-72] | Live reattachment, JSON-safe saved state, original-backend deletion, and replacement continuation with a fresh agent run. | [Pause scenario][pause-scenario], `test_pause_continue_serializes_state_and_restores_into_a_fresh_run` and negative cases in the [sample tests][scenario-tests], plus [adapter resume guidance][adapter-guide]. |
| **7.1.4** | [#66][issue-66] | Workspace-first positioning and a task-oriented scenario catalog, preserving `workspace-edit` as the diagnostic default. | [Root README][root-guide], [SDK sample guide][sample-guide], both provider guides, and provider registry/parser tests in [the sample test directory][sample-test-directory]. |
| **7.2.1** | [#73][issue-73] | Explicit SDK support bounds and recurring exercised-version checks. | [Compatibility matrix][adapter-guide], [SDK contracts][sdk-tests], [distribution tests][distribution-tests], and the installed-package command below. |
| **7.2.2** | [#71][issue-71] | A typed, host-owned function tool composes with the separate MemSandbox capability. | [Public composition example][composition-guide] and [composition tests][composition-tests] cover five-tool exposure, duplicate-name rejection before invocation, host dependency failure, and cleanup. |
| **7.2.3** | [#73][issue-73] | Clean installed artifacts exercise public imports and a complete model-free document workflow outside the checkout. | [Distribution tests][distribution-tests] rebuild wheels from sdists, install the exact SDK/Pydantic rows, run `pip check`, and execute the copied [probe][installed-probe] with Python isolated mode. |
| **7.2.4** | [#77][issue-77] | Integration blockers are classified; no supported external demand justifies another SDK. | The [blocker register and evidence sources][assessment] distinguish host retrieval, model behavior, provider limitations, and actual product-surface requirements. |
| **7.2.5** | [#67][issue-67] | Dependency-free core and independently versioned OpenAI adapter distributions, preserving serialized identities. | [Package-split design][package-split], [distribution tests][distribution-tests], [session tests][session-tests], and the [adapter migration guide][adapter-guide]. No version changes are needed for closure. |
| **7.3.1** | [#75][issue-75] | Complete deterministic showcase conformance without duplicating every shared invariant in every scenario. | [Conformance matrix][scenario-guide], all-registry [sample tests][scenario-tests], shared [capability bounds/failures][capability-tests], and [core stale-patch rejection][patch-tests]. |
| **7.3.2** | [#76][issue-76] | Two approved one-shot live observations preserve both success and failed exact verification. | [Retained commands, source, environment, and results][live-results], the generated [document report][document-report], and [guarded handoff report][handoff-report]. No new run or retry is part of #78. |
| **7.3.3** | [#76][issue-76] | Workspace lifecycle/tool timing remains separate from model latency and usage. | [Evaluation timing semantics][evaluation-guide] and [retained timing tables][live-results]. Neither retained fixture exercises snapshots; both report unavailable monetary cost and demonstrate no Milestone 6 capacity trigger. |
| **7.3.4** | [#77][issue-77] | Defer the next capability until a linked, reproducible workflow meets an explicit review trigger. | The [decision, triggers, authority, and future requirements][assessment] are the disposition, not implementation authorization for Milestones 8-12. |

## Deterministic workflow evidence

The authoritative verifier is `runner._verify_artifacts`: it reads each
expected path through the public `SandboxSession` interface and raises
`ScenarioVerificationError` on any byte mismatch. The all-registry test uses
test-owned expected paths and bytes rather than accepting a runner result or
model final response without independent assertions.

The three representative workflows retain distinct semantics:

| Workflow | Required host evidence | Negative and lifecycle evidence |
|---|---|---|
| `document-review` | Preserved source/instructions, exactly corrected draft, path-linked reviewer artifact, editor-before-reviewer order. | Real policy-denied protected writes and stale hashes do not mutate protected state; reviewer failure preserves completed work for inspection and then cleans up. |
| `independent-reviewers` | Distinct baseline/risk/clarity handles, exact sibling outputs, unchanged restored baseline, and explicit host-selected artifacts. | Shared snapshot-path failure/cancellation tests verify preserved inspectable state and cleanup. Logical workspace isolation is not arbitrary-code isolation. |
| `pause-continue` | Same-handle live attachment followed by JSON round trip, a distinct restored replacement, preserved checkpoint, and a fresh continuation conversation. | Invalid/missing state, dependency failure, cancellation during restore, and source/replacement cleanup remain covered. An in-memory store does not survive process loss. |

With the declared environment installed, these PowerShell commands require no
provider credentials or inference calls:

```powershell
uv run python -m samples.openai_agents_sdk.providers.openai --list-scenarios
uv run python -m samples.openai_agents_sdk.providers.azure_openai --list-scenarios
uv run pytest tests\samples tests\integrations\openai_agents\test_capability.py tests\integrations\openai_agents\test_tool_composition.py
```

The [conformance matrix][scenario-guide] names which rejection, failure, bounds,
and cancellation cases are scenario-owned, shared, or not applicable. This
milestone does not turn an inapplicable case into simulated prose-only evidence.

## Installed-package and SDK evidence

The unchanged package versions are core `0.2.0` and adapter `0.1.0`.
Adapter `0.1.x` declares `mem-sandbox>=0.2.0,<0.3`,
`openai-agents>=0.22.0,<0.23`, and `pydantic>=2.12.2,<3`.
The exercised SDK rows are `0.22.0` and `0.22.2`, each with the Pydantic
`2.12.2` floor. Other allowed SDK patches are not claimed as individually tested.

```powershell
$env:MEM_SANDBOX_OPENAI_AGENTS_TEST_VERSIONS = '0.22.0,0.22.2'
uv run pytest tests\integrations\openai_agents\test_distribution_artifacts.py
```

The test builds both distributions, rebuilds their wheels from sdists, checks
package metadata/content, creates clean environments, and runs the installed
probe without editable or checkout imports. The probe verifies the SDK runner,
exact tool surface, snapshot/state lifecycle, document workflow, and cleanup.
The [release guide][contributing] keeps this compatibility check recurring.

Package retrieval can require host network access even though the probe makes
no model-network requests. A package-CDN failure is a retrieval failure, not
SDK incompatibility or a passing compatibility row. Use an operator-approved
package source or retain the actual CI result; do not skip a row, widen support,
or change dependencies just to make the evidence look successful.

The [#73 validation record][historical-sdk-validation] distinguishes successful
exact-version probes from local CDN failures before probe execution. The
[#71 record][historical-composition-validation] retains an aggregate-run
timeout that passed in isolation, alongside targeted and offline clean-install
success. The [#77 record][historical-closure-validation] retains 798 passes,
one deselection, and four package setup errors. None of those historical local
aggregates is rewritten as an uninterrupted green full-suite run.

Host-tool composition does not grant MemSandbox host access. The application
injects its own typed gateway, model, and service, rejects tool-name collisions,
and retains ownership of credentials, network authority, and cleanup. Full
shells, host filesystems, mounts, PTYs, ports, arbitrary Python, and SDK default
capabilities are not implied by successful composition.

## Retained live observations

The [2026-09-13 Azure OpenAI result set][live-results] contains the two runs
authorized for #76 at source
`9148d44ee281d3a6d5ccc3858ce117298ac128ff`. Both used Python `3.12.12`,
OpenAI Agents SDK `0.22.0`, OpenAI Python SDK `3.8.0`, deployment
`gpt-5.4-mini-global`, and API version `2024-12-01-preview` on Windows 11.
Its retained approval link and exact commands are historical authorization for
those runs, not permission to run them again.

| Scenario | Retained outcome | Usage and limits |
|---|---|---|
| `document-review` | Both stages completed, but final draft verification failed. Artifact hashes remain `verification_incomplete`; three invalid patches and one policy-denied command were retained. | 55,256 tokens; monetary cost unavailable. Incomplete verification does not assert that preserved source artifacts were modified or establish a missing sandbox operation. |
| `multi-agent-handoff` | Succeeded with 3/3 exact artifact checks; two invalid patches and one immediately linked successful repair were retained. | 51,032 tokens; monetary cost unavailable. This is one provider- and machine-specific observation, not a reliability guarantee. |

The reports separate seed, read, mutation, constrained execution, snapshot,
verification, cleanup, provider-model, and end-to-end timing. End-to-end
overlaps the other categories and is not their sum. Neither fixture exercises
snapshot persist/restore, so those measurements are explicitly zero. The
observations are non-deterministic, non-gating, and not comparable across
uncontrolled runs. They do not authorize optimization, content offload, or
another live retry.

## Documentation reconciliation

| Surface | Reconciled contract |
|---|---|
| [Root README][root-guide] | Workspace-first task positioning, scenario discovery, installed public usage, and clearly optional future capabilities. |
| [Adapter README][adapter-guide] | Declared versus exercised versions, public composition, explicit unsupported defaults, host lifecycle ownership, and process-local snapshot limits. |
| [SDK sample README][sample-guide] | Three representative workflows, unchanged default/name behavior, exact host verification, and repository-only sample ownership. |
| [Scenario README][scenario-guide] | Complete conformance matrix, baseline/sibling isolation, explicit selection, fresh continuation, and negative-case ownership. |
| [OpenAI][openai-guide] and [Azure OpenAI][azure-guide] provider READMEs | Supported provider configuration, credential-free discovery, host cleanup, and billable/non-deterministic live behavior. |
| [Product-validation README][validation-guide] and [evaluation README][evaluation-guide] | Separate correctness, performance, and live-evaluation evidence; retained failures and unavailable cost stay explicit. |
| [Documentation index][documentation-index] and [PLAN][plan] | Correct milestone navigation, completed work-item/exit mapping, and an explicit tracker completion gate. |
| Workspace-root product manifest | The human-approved wording clarification and before/after digest are recorded in the [closure record][closure-record]. This workspace-owned guidance is not a file in the repository or either distribution; no nonexistent repository manifest link is implied. |

The shipped in-memory snapshot store keeps payloads within its process.
Serialized session state references retained snapshot data; it does not itself
include that payload, model conversations, or process-loss recovery. A future
durable store and state-custody implementation remain host-owned work with their
own evidence and authority requirements.

## Complete repository validation

Run from the repository root with the declared Python/uv toolchain. Environment
preparation follows the [contribution guide][contributing] and
[Quality workflow][quality]. These PowerShell commands mirror the required
validation surfaces:

```powershell
$env:HYPOTHESIS_PROFILE = 'ci'
$env:MEM_SANDBOX_OPENAI_AGENTS_TEST_VERSIONS = '0.22.0,0.22.2'
uv run ruff format --check benchmarks evaluations packages samples src tests
uv run ruff check benchmarks evaluations packages samples src tests
uv run pyright benchmarks evaluations packages\openai-agents\src samples src tests
uv run pytest -m "not benchmark_smoke"
uv run pytest -m benchmark_smoke
uv build --all-packages --no-sources
```

Local Windows/Python 3.12 results do not stand in for the separate Linux/Windows
and Python 3.12/3.14 CI matrix. The [closure record][closure-record] retains
actual commands, counts, environment, artifact outcomes, and any failed or
outstanding checks for the pushed revision. It is the current-issue validation
reference; the live reports above are historical, separate observations.

## Tracker completion and maintenance

At the 2026-09-15 source reconciliation, #70 had exactly eight native children
(#71-#78), seven completed and #78 open, with no native blocked-by edges.
#66/#67 are completed baselines rather than children. #78's seven native
blockers (#71-#77) were all completed. Historical prerequisite prose on #75/#76
does not exactly match their current empty blocked-by lists; all referenced
prerequisites are already completed, and this documentation change does not
rewrite those relationships.

The [tracker contract][tracker] requires the focused child work and final
evidence, not merely completed PLAN checkboxes. Before closing #70:

1. Verify the source deliveries and all native child/dependency state, requiring
   `state_reason=completed` rather than cancellation or duplicate closure.
2. Verify #78's documentation/evidence change is merged, its required checks are
   satisfied, and its final validation and next-capability decision are linked
   on the tracker.
3. Finish #78 and then the tracker through the completion flow, preserving
   unrelated work and the recorded decision to defer extensions.

This document does not itself close either issue, supply merge approval, change
the native graph, or authorize the next milestone. Parenthood and native
blocked-by relationships remain distinct.

Keep the work-item mapping and owning READMEs aligned when a scenario, interface,
compatibility row, evidence source, or decision changes. Preserve historical
report identity and failed observations rather than rewriting them with newer
results.

[tracker]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/70
[issue-66]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/66
[issue-67]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/67
[issue-71]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/71
[issue-72]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/72
[issue-73]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/73
[issue-74]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/74
[issue-75]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/75
[issue-76]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/76
[issue-77]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/77
[issue-78]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/78
[pr-68]: https://github.com/hahahahahaiyiwen/mem-sandbox/pull/68
[pr-69]: https://github.com/hahahahahaiyiwen/mem-sandbox/pull/69
[pr-79]: https://github.com/hahahahahaiyiwen/mem-sandbox/pull/79
[pr-80]: https://github.com/hahahahahaiyiwen/mem-sandbox/pull/80
[pr-81]: https://github.com/hahahahahaiyiwen/mem-sandbox/pull/81
[pr-82]: https://github.com/hahahahahaiyiwen/mem-sandbox/pull/82
[pr-83]: https://github.com/hahahahahaiyiwen/mem-sandbox/pull/83
[pr-84]: https://github.com/hahahahahaiyiwen/mem-sandbox/pull/84
[pr-87]: https://github.com/hahahahahaiyiwen/mem-sandbox/pull/87
[historical-sdk-validation]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/73#issuecomment-5638159126
[historical-composition-validation]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/71#issuecomment-5638917393
[historical-closure-validation]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/77#issuecomment-5656559484
[closure-record]: https://github.com/hahahahahaiyiwen/mem-sandbox/issues/78#issuecomment-5672674893
[plan]: ../PLAN.md#11-milestone-7-workspace-first-showcase-and-integration-usability
[root-guide]: ../../README.md
[documentation-index]: ../README.md
[validation-guide]: ./README.md
[assessment]: ./workflow-blocker-assessment.md
[adapter-guide]: ../../packages/openai-agents/README.md
[composition-guide]: ../../packages/openai-agents/README.md#compose-with-a-host-owned-function-tool
[sample-guide]: ../../samples/openai_agents_sdk/README.md
[scenario-guide]: ../../samples/openai_agents_sdk/scenarios/README.md
[openai-guide]: ../../samples/openai_agents_sdk/providers/openai/README.md
[azure-guide]: ../../samples/openai_agents_sdk/providers/azure_openai/README.md
[runner]: ../../samples/openai_agents_sdk/runner.py
[document-scenario]: ../../samples/openai_agents_sdk/scenarios/document_review.py
[reviewer-scenario]: ../../samples/openai_agents_sdk/scenarios/independent_reviewers.py
[pause-scenario]: ../../samples/openai_agents_sdk/scenarios/pause_continue.py
[sample-test-directory]: ../../tests/samples
[scenario-tests]: ../../tests/samples/test_openai_agents_sdk_azure_openai.py
[capability-tests]: ../../tests/integrations/openai_agents/test_capability.py
[composition-tests]: ../../tests/integrations/openai_agents/test_tool_composition.py
[distribution-tests]: ../../tests/integrations/openai_agents/test_distribution_artifacts.py
[installed-probe]: ../../tests/integrations/openai_agents/installed_package_probe.py
[sdk-tests]: ../../tests/integrations/openai_agents/test_sdk_contract.py
[session-tests]: ../../tests/integrations/openai_agents/test_session_adapter.py
[patch-tests]: ../../tests/unit/workspace/test_patching.py
[package-split]: ../OPENAI_AGENTS_PACKAGE_SPLIT.md
[evaluation-guide]: ../../evaluations/workspace_showcase/README.md
[live-results]: ../../evaluations/workspace_showcase/results/20260913T183046Z-azure-openai/README.md
[document-report]: ../../evaluations/workspace_showcase/results/20260913T183046Z-azure-openai/document-review/REPORT.md
[handoff-report]: ../../evaluations/workspace_showcase/results/20260913T183046Z-azure-openai/multi-agent-handoff/REPORT.md
[contributing]: ../../CONTRIBUTING.md
[quality]: ../../.github/workflows/quality.yml
