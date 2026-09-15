# Current Artifact Workflows and Evidence Gaps

**Assessment date:** 2026-09-15  
**Source baseline:** `be28dd5ff532556d419d4e1df671e746adfb7e25`  
**Scope:** [#91](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/91), the
documentation-only baseline for Milestone 8 planning.

**Finding:** Existing host-owned bytes, binary operations, manifests and portable
archives support the bounded recipe below. This audit establishes no new
[workflow-blocker review trigger](./workflow-blocker-assessment.md#review-triggers).
Missing convenience APIs are not, without a blocked workflow, implementation authority.

## Current behavior and ownership

Paths below refer to the recorded source revision. Existing tests describe their
asserted scope, not a claim that every future artifact requirement is implemented.

| Surface | Current behavior and boundary | Source / evidence |
|---|---|---|
| Host seeding | `CreateSandboxRequest.initial_files` accepts a tuple of `WorkspaceSeedFile(path, bytes)`. The service/factory creates and starts a workspace; this is not a host-path loader. Empty directories use native directory operations. | [Service models](../../src/mem_sandbox/service/models.py), [factory](../../src/mem_sandbox/service/factory.py), [service tests](../../tests/unit/service/test_service.py) |
| Binary IO | Session `read_bytes` / `write_bytes` preserve bytes; write preconditions and workspace limits remain authoritative. Model-facing text reads are a different bounded UTF-8 surface. | [Session requests](../../src/mem_sandbox/session/models.py), [session implementation](../../src/mem_sandbox/session/session.py), [workspace reads](../../tests/unit/workspace/test_reads.py), [mutation tests](../../tests/unit/workspace/test_mutations.py) |
| Portable export | `WorkspaceArchivePort.export_portable_archive` returns complete in-memory tar bytes plus format version, workspace revision and root hash in `WorkspaceArchiveData`. Canonical export includes files and empty directories, with normalized metadata. | [Workspace port](../../src/mem_sandbox/workspace/ports.py), [archive model](../../src/mem_sandbox/workspace/models.py), [codec tests](../../tests/unit/workspace/test_portable_archive_codec.py) |
| Portable import | Workspace-owned parsing accepts the supported uncompressed/gzip tar profile, prepares a validated candidate, then publishes atomically. It is whole-tree restore, not an overlay, Git checkout or general archive-extraction API. | [Codec](../../src/mem_sandbox/workspace/archive_codec.py), [restore tests](../../tests/unit/workspace/test_portable_archive_restore.py), [property tests](../../tests/property/workspace/test_portable_archive_properties.py) |
| Conditional publication | Session restore can require both expected current revision and root hash. A mismatch raises `SessionWorkspaceChanged` before publication; the pair must be supplied together. Restoring historical state can rewind the revision, so revision alone is not a global monotonic identity. | [Session operations](../../tests/unit/session/test_session_operations.py), [prepared restore tests](../../tests/unit/workspace/test_prepared_restore.py) |
| SDK manifests | The OpenAI adapter translates synthetic `File` and `Dir` entries, not host files, mounts or `GitRepo`. Live manifest application stages a copy and publishes with core revision/hash guards; staging cleanup failure prevents publication. | [Adapter](../../packages/openai-agents/src/mem_sandbox_openai_agents/adapter.py), [manifest tests](../../tests/integrations/openai_agents/test_manifest_profile.py) |
| SDK binary/archives | Adapter binary streams have their own stream bounds. Persistence/hydration uses core archives and associated metadata; the generic SDK `extract` method is explicitly unsupported. | [Adapter tests](../../tests/integrations/openai_agents/test_session_adapter.py), [adapter guide](../../packages/openai-agents/README.md) |
| Output collection | A trusted host can read named result paths and verify exact bytes/hashes, or retain a complete portable archive. Selecting paths in host code does not create an atomic selected-export or input-bound diff API. | [Host-tool composition](../../packages/openai-agents/README.md#compose-with-a-host-owned-function-tool), [document-review evidence](./milestone-7-exit-evidence.md), recipe below |

The core owns filesystem and session semantics; adapter code owns SDK translation,
not tar validation. Host applications own input acquisition, authentication,
credentials, destination selection and any decision to save returned bytes. The
default four-tool model capability grants none of those host powers.

## Bounds, consistency and failure interpretation

`WorkspaceLimits` defaults include 4 MiB per file, 16 MiB total file content,
10,000 nodes, 4,096 path bytes, 255 segment bytes and 32 MiB snapshot/archive bytes.
These are separate positive limits, not one transfer budget. The codec bounds input,
gzip expansion, encoded output, member/path metadata and the resulting workspace.
Archives are buffered in memory; a bounded adapter stream is not an unbounded streaming
importer. See the [workspace design](../components/workspace/README.md) for the profile.

| Condition | Asserted behavior / important limit | Evidence |
|---|---|---|
| Unsafe or malformed archive | Reject traversal, aliases, duplicates/type conflicts, unsupported node types, sparse/unsupported records and malformed padding before publication. No host filesystem extraction. | `test_archive_import_rejects_*`, `test_archive_codec_never_uses_host_filesystem_extraction` in [codec tests](../../tests/unit/workspace/test_portable_archive_codec.py) |
| Bounds | Exact boundary values and one-over cases distinguish file/node/path/input limits; gzip expansion is separately bounded. Limits do not imply cumulative operation/session accounting. | `test_archive_import_accepts_exact_file_node_path_segment_and_input_limits`, `test_archive_import_rejects_each_limit_one_over_without_conflating_limits`, `test_gzip_expansion_is_bounded_separately_from_compressed_input` in codec tests |
| Invalid restore metadata or candidate | Format/hash mismatch or failed preparation leaves live state unchanged; prepared state is bound to its workspace and cannot be replayed by copying it. | [Restore tests](../../tests/unit/workspace/test_portable_archive_restore.py) |
| Stale target | Supplying an old revision/hash pair rejects publication. Omitting the pair requests unconditional whole-tree replacement, not preservation of concurrent edits. | `test_portable_archive_conditional_restore_rejects_changed_workspace_identity` in [session tests](../../tests/unit/session/test_session_operations.py) |
| Cancellation/deadline | Cancellation before publication must not publish a candidate. Once commit completes, the session treats completion as authoritative rather than claiming cancellation rolled it back. | Restore waiter/deadline tests; `test_portable_archive_commit_completion_is_authoritative_during_native_cancellation` and `test_portable_archive_completion_event_consumes_native_cancellation` in session tests |
| Required event failure | Failed start delivery prevents collaborator mutation. Failed terminal delivery can report an error after the mutation committed; do not retry blindly or infer rollback from an exception. | `test_required_event_failures_follow_start_and_terminal_rules` in session tests; [snapshot event/atomicity tests](../../tests/integration/session/test_snapshot_restore_atomicity.py) |
| Unsupported SDK input | Invalid manifest features/quota failures reject before allocation or target mutation; failed staging cleanup prevents target publication. | [Manifest tests](../../tests/integrations/openai_agents/test_manifest_profile.py) |

Repository control data needs special care: the generic archive path grammar does not
ban a relative `.git/config` member merely because its name is repository-related.
`GitRepo` manifest rejection is not the same guarantee. A future repository importer
would need separately designed filtering; this audit changes neither the path grammar
nor archive compatibility.

One workspace method is the mutation boundary. A sequence of host writes or selected
reads is not a multi-operation transaction. The session gate protects operations
through that session; it is not a lease over arbitrary direct workspace collaborators.
The recipe exclusively owns its sessions, so no concurrent writer can interleave its
selected reads.

## Reproducible host input/output recipe

This repository-only recipe packages a small release-note artifact and binary companion,
then copies the complete result tree into a second sandbox. Inputs are explicit fixture
bytes standing in for content already acquired by the application. It demonstrates
integration mechanics, not a reported user's blocked workflow.

From the repository root after the
[declared development setup](../../CONTRIBUTING.md#development-setup), run the Python
block with `uv run python` (PowerShell: pipe a single-quoted here-string to
`uv run python -`). It imports the existing repository sample composition root; the
`samples` package is not an installed public API. All sandbox interactions use public
core types, and no SDK, model, credential, Git command or external service is needed by
the recipe. Do not run with Python assertions disabled.

```python
import asyncio
import json

from mem_sandbox.service import (
    CreateSandboxRequest,
    OwnerId,
    SandboxOptions,
    WorkspaceSeedFile,
)
from mem_sandbox.session import (
    CreateDirectoryRequest,
    ReadBytesRequest,
    WriteBytesRequest,
)
from mem_sandbox.workspace import PathMustNotExist, WorkspaceLimits
from samples.shared.service import create_sample_service


async def main() -> None:
    service = create_sample_service()
    options = SandboxOptions(
        workspace_limits=WorkspaceLimits(
            max_file_bytes=1024,
            max_total_bytes=8192,
            max_nodes=32,
            max_snapshot_bytes=65536,
        )
    )
    try:
        handle = await service.create(
            CreateSandboxRequest(
                owner_id=OwnerId("artifact-audit"),
                options=options,
                initial_files=(
                    WorkspaceSeedFile("inputs/notes.txt", b"Release 1\n"),
                    WorkspaceSeedFile("inputs/logo.bin", b"\x00\xfflogo"),
                ),
            )
        )
        source = await service.get_session(handle)
        notes = await source.read_bytes(ReadBytesRequest(path="inputs/notes.txt"))
        binary = await source.read_bytes(ReadBytesRequest(path="inputs/logo.bin"))
        await source.create_directory(
            CreateDirectoryRequest(path="artifacts/empty", create_parents=True)
        )
        for path, content in (
            ("artifacts/notes.txt", b"Reviewed\n" + notes.content),
            ("artifacts/logo.bin", binary.content),
        ):
            await source.write_bytes(
                WriteBytesRequest(
                    path=path, content=content, precondition=PathMustNotExist()
                )
            )
        expected = {
            "artifacts/notes.txt": b"Reviewed\nRelease 1\n",
            "artifacts/logo.bin": b"\x00\xfflogo",
        }
        for path, content in expected.items():
            assert (await source.read_bytes(ReadBytesRequest(path=path))).content == content

        archive = await source.export_portable_archive()
        replacement_handle = await service.create(
            CreateSandboxRequest(owner_id=OwnerId("artifact-audit"), options=options)
        )
        target = await service.get_session(replacement_handle)
        empty = await target.export_portable_archive()
        await target.restore_portable_archive(
            archive,
            expected_current_revision=empty.workspace_revision,
            expected_current_root_hash=empty.root_hash,
        )
        restored = await target.export_portable_archive()
        assert restored.encoded == archive.encoded
        assert restored.root_hash == archive.root_hash
        assert restored.workspace_revision == archive.workspace_revision
        for path, content in expected.items():
            assert (await target.read_bytes(ReadBytesRequest(path=path))).content == content
        print(json.dumps({
            "archive_round_trip": True,
            "binary_equal": True,
            "verified_outputs": len(expected),
        }, sort_keys=True))
    finally:
        await service.close()


asyncio.run(main())
```

Expected output:

```json
{"archive_round_trip": true, "binary_equal": true, "verified_outputs": 2}
```

Canonical archive/root equality preserves the whole tree, including the input files
and empty directory; this is not a selected-artifact archive. The two named output
assertions are independent of model prose. `WorkspaceArchiveData` retains both tar
bytes and restore metadata; a bare tar file is not interchangeable with this value.
The host may retain that value, but portable content does not make the shipped
snapshot-spec reference survive process loss or preserve conversation/runner state.
See the [snapshot-backed resume guide](../../packages/openai-agents/README.md#optional-snapshot-and-resume-support).

## Gap register and disposition

Frequency/severity below describes the supplied evidence, not a product-wide survey.
No new customer, collaborator or deployment-demand report was supplied to this audit.

| Candidate gap | Evidence and classification | Workaround / authority | Disposition |
|---|---|---|---|
| Basic bounded input and verified output | The recipe uses existing core APIs; prior manifest/binary/archive tests cover the adapter path. One deterministic integration recipe, not market-demand evidence. | Application supplies bytes, chooses output paths and verifies bytes. | No missing operation established for this workflow. |
| Atomic selected export or input-bound diff | No such public operation in the inspected archive/session ports; supported whole-tree archives and individual reads are distinct. Product-surface limitation; blocked-workflow frequency/severity unknown. | Exclusive host orchestration and full archives where suitable. A concurrent consumer would need a reproducible workflow and explicit consistency requirements. | Conditional design input to #92, not a satisfied trigger. |
| Repository filtering or retrieval | Generic archives are not a `.git`-filtering importer; SDK `GitRepo` and generic extraction are unsupported. No blocked repository workflow supplied. | Host-owned preparation outside the sandbox, with its own authority and validation; not an automatic safe-import claim. | Conditional design input to #92/#94; no provider/API change authorized. |
| Whole-buffer transfer / cumulative budgets | Encoded bytes and prepared workspace are bounded, but not an unbounded streaming path or a cumulative transfer ledger. No measured over-limit product workload supplied. | Stay within documented component limits; do not widen limits or advertise streaming/cumulative enforcement. | Potential consumer evidence for #93, not a reason to implement accounting now. |
| Host package retrieval | Milestone 7 retained four local clean-install retrieval failures; repository CI supplied installed-package evidence. Host-environment friction, not workspace IO failure. | Operator-controlled dependency delivery/CI, outside sandbox authority. | Repeated CDN failure alone does not meet #77. |
| Model/provider behavior | Retained document-review live run failed exact verification; handoff run passed 3/3 artifacts. Costs were unavailable, not zero. These are historical observations, not new runs. | Exact host checks and deterministic conformance; another live run needs separate authority. | No evidence that a new artifact operation fixes those failures. |
| Process-loss recovery | Portable content and process-local snapshot references are distinct; no deployment-demand evidence was supplied here. | Host custody plus a separately designed persistence provider if required. | Remains the separate #56 gate, not a Milestone 8 importer requirement. |

The last three rows reuse the dated
[Milestone 7 assessment](./workflow-blocker-assessment.md) and
[exit-evidence index](./milestone-7-exit-evidence.md); this audit does not relabel those
outcomes or repeat live evaluations.

**Disposition: no new capability trigger met by the available evidence.**
Continue the approved documentation/design children #92-#95, labeling proposed
contracts as conditional. Reopen capability selection only with a linked reproducible
workflow that cannot reasonably use current host seeding/verified collection, and
separately approve its bounded implementation. Neither this audit nor completion of
its planning siblings completes Milestone 8 exit criteria.

## Rechecking the evidence

Use the current repository toolchain, record its revision, and retain command results
with the issue/PR rather than silently treating historical results as fresh:

```console
uv run pytest tests/unit/workspace/test_portable_archive_codec.py tests/unit/workspace/test_portable_archive_restore.py tests/property/workspace/test_portable_archive_properties.py tests/integrations/openai_agents/test_manifest_profile.py tests/unit/session/test_session_operations.py
```

Run the exact recipe above and compare its JSON output. When changing any owning
behavior, update its tests and module README; when only revising this audit, keep the
source links, limitations, evidence dates and current-versus-proposed labels accurate.
