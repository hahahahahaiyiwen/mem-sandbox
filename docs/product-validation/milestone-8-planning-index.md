# Milestone 8 Planning Evidence and Gate Map

**Index date:** 2026-09-15  
**Source baseline:** `2eee27db783faa925c1e6a34ad34e290368d4980`  
**Scope:** [#95](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/95), the
documentation-only reconciliation child for Milestone 8 planning.

## Planning tranche status

The five-child planning tranche for
[Milestone 8](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/53) is a
documentation/design sequence. Completing it records evidence, decisions, and gates; it
does not ship a runtime capability and does not complete the parent milestone.

| Child | Deliverable | Outcome | Evidence |
|---|---|---|---|
| [#91](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/91) | Current artifact workflow audit | Existing host-owned bytes, binary IO and portable archives support the bounded recipe; no new #77 trigger met. | [Audit](./artifact-workflow-audit.md), [PR #96](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/96) |
| [#92](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/92) | Bounded artifact exchange design | Reuse-first current path; content-tree input, selected export and input-bound diff remain conditional sketches. | [Design](./artifact-exchange-design.md), [PR #97](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/97) |
| [#93](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/93) | Authority, policy, accounting and event design | Host-selected grants, prepared facts, settlement paths and event facts are documented as conditional requirements. | [Design](./authority-accounting-design.md), [PR #98](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/98) |
| [#94](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/94) | Conditional Git retrieval decision | Git retrieval implementation is deferred until a concrete trigger and separate detailed approval exist. | [Decision](./git-retrieval-decision.md), [PR #99](https://github.com/hahahahahaiyiwen/mem-sandbox/pull/99) |
| [#95](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/95) | Planning evidence and future gate map | This index reconciles the planning tranche and preserves remaining implementation gates. | This document |

Decision provenance remains the parent
[#53 planning approval](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/53#issuecomment-5685248665),
the [#95 reconciliation design](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/95#issuecomment-5687818220),
and the issue-specific child design records linked from each issue. The recorded
planning approval does not approve future API, transport, provider, ledger, profile,
network, process, SDK, or live-evaluation choices.

## Exit criteria map

| Milestone 8 exit criterion | Existing evidence | Conditional design | Missing delivery evidence | Executable future checks | Next owner/action |
|---|---|---|---|---|---|
| **8.9.1** The default virtual profile has exactly its existing host-free authority. | High-level design and current profile keep the default virtual profile network-free, process-free and host-filesystem-free. | #93 records default denial and no authority from snapshots, model input, archive content or `OwnerId`. | No new capability grant is shipped or needed for the default profile. | Regression checks for denied host path/network/process/repository operations and unchanged model-facing capability descriptions. | Preserve current profile; any future capability must be opt-in and separately tested. |
| **8.9.2** Optional capabilities are immutable, host-selected, accurately described, and composed without framework dependencies or dynamic model-controlled loading. | Current adapters bind only explicitly supported capabilities and core remains framework-neutral. | #93 specifies create/resume grant selection, narrowing, and no dynamic model-controlled extension loading. | No runtime grant model for a new artifact/repository capability exists. | Grant construction/resume narrowing tests, snapshot non-escalation tests, adapter description conformance, and dependency-boundary checks. | Future capability issue owns typed grant contracts and denial tests. |
| **8.9.3** Resource reservation and settlement are typed, cumulative, atomic, and proven for every terminal path of each shipped capability. Record deferred dimensions and their owning milestones rather than requiring unused accounting implementations. | Workspace, command, archive and operation hard limits already bound current operations. | #93 lists conditional materialized input/output bytes, entry counts, cumulative transfer and exact-once settlement semantics. | No cumulative ledger or durable exact-once settlement exists for unimplemented capabilities. | Reservation denied, exact-limit/one-over, cancellation, timeout, commit-success, terminal-event-failure and recovery/re-read tests. | Implement only dimensions consumed by the first approved capability; defer tenant/session budgets until needed. |
| **8.9.4** Host-controlled repository import and export preserve workspace containment, quotas, hashes, revisions, and atomicity without granting host Git, filesystem, or network authority. | #91 demonstrates current host seeding, binary reads/writes, full archives and revision/root-hash restore guards. | #92 sketches content-tree input, selected export and input-bound diff with atomicity, provenance and failure semantics. | No new facade, selected export, input-bound diff, content-tree publication API or repository importer exists. | Archive/content-tree invalid-input tests, stale-baseline rejection, path containment, quota/hash/revision assertions and no-host-destination checks. | Use current composition unless a #77 trigger proves a gap; then approve one narrow facade issue. |
| **8.9.5** Policy re-evaluation records which decisions belong to the session and which remain authoritative at each resource boundary. | Current `SessionPolicyEngine` gates implemented session operations and keeps component invariants in owning modules. | #93 maps candidate artifact actions to session admission, resource admission, prepared facts, event facts and settlement boundaries. | No artifact-specific policy port, prepared-fact implementation or resource-boundary re-evaluation exists. | Policy denial before collaborator calls, evaluator-failure fail-closed, prepared-fact ownership, event redaction and collaborator-order tests. | First triggered protected action defines its narrow module-owned port and fake/spy tests. |
| **8.9.6** Git retrieval has an explicit design decision and separately scoped implementation acceptance criteria. Planned retrieval is not advertised as available until its provider, update semantics, and conformance are implemented. | Current supported path is host-prepared input; no Git retrieval capability is shipped. | #94 compares initiators/transports and defers retrieval until trigger plus approval; provider request/result and fake-provider tests are conditional. | No provider, Git backend, archive API integration, live retrieval, credential flow, refresh operation, LFS/submodule handling or cleanup implementation exists. | Fake-provider no-contact/denial, pinned commit, ref-race, unsupported metadata, retrieval failure, import failure, cleanup failure and redaction tests. | Require linked #77 trigger, detailed design, fake-provider conformance and explicit live-evaluation permission. |

Executable checks named in the child documents remain future evidence requirements unless
the current PR explicitly ran them. Documentation-only planning PRs may verify links and
scope, but they cannot claim passing behavior for unimplemented surfaces.

## Future implementation gate

A future Milestone 8 implementation slice can start only when all of the following are
true:

1. A linked reproducible workflow satisfies a #77 review trigger and cannot reasonably
   use current host seeding, host reads, and whole archives.
2. A separate design approval selects one narrow slice, its initiating actor, interface
   owner, authority source, policy/admission facts, accounting dimensions, event facts,
   failure semantics and README/test obligations.
3. The issue remains scoped to one consuming module boundary; no global interface bucket,
   generic policy language, service locator, dynamic model-loaded extension, or
   speculative ledger is introduced.
4. Behavior tests are defined before implementation for happy, denial, invalid input,
   exact limit/one-over, stale baseline, race, cancellation, pre-publication failure,
   post-commit reporting, cleanup and collaborator-order cases.
5. Any live provider, paid model, private repository, network, process, second SDK, or
   remote mutation evidence receives explicit authorization.

If these conditions are not met, the correct next action is to keep the capability
deferred and continue using host-controlled composition.

## Parent tracker disposition

#53 remains open after this planning tranche unless a separate completion action proves
the unchanged full Milestone 8 acceptance criteria. The parent still needs delivery
evidence for every shipped capability, not just accepted planning documents. Closing a
planning child, adding a design table, or documenting a defer outcome is not proof that a
runtime capability shipped.

When all children are complete, #53 may be reconciled out of dependency wait and should
continue to show explicit remaining evidence gaps and deferred capability decisions until
the full tracker is independently satisfied.
