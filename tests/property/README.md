# Property and Stateful Test Design

**Status:** Issue #21 generated core coverage implemented

## Purpose

This test boundary uses Hypothesis to generate bounded operation sequences and prove
component invariants that fixed examples cannot cover exhaustively. Models are
deterministic, shrinkable, and independent of production-private state.

Hypothesis is a development-only dependency. The runtime package remains dependency-free.

## Oracle rules

Generated tests:

- maintain a small test-owned reference model;
- assert only public immutable results, recursive list/read projections, statistics,
  snapshots, and collaborator-owned traces;
- compare the complete public projection before and after every expected failure;
- generate valid and invalid categories structurally instead of filtering arbitrary
  strings;
- use logical clocks, never sleeps, for expiry;
- use one test-owned `asyncio.Runner` per async state machine;
- cap bytes, paths, nodes, units, stages, examples, and state-machine steps;
- never invoke a host shell/process/filesystem or compare behavior to the host OS;
- retain focused fixed regressions instead of duplicating them as generated examples.

When Hypothesis finds a defect, its minimized example is added to the owning focused
regression test when the defect is fixed. The local example database and CI reproduction
blob support diagnosis but are not substitutes for a permanent regression.

## State machines

### `SandboxPathStateMachine`

**Model:** cwd segments, canonical resolved paths, and small UTF-8 segment/path limits.

**Rules:** resolve absolute/relative paths, normalize repeated separators and `.`, join a
child, move modeled cwd, and submit one invalid path category.

**Invariants:**

- every accepted path is canonical, absolute, and under `/workspace`;
- root parent is root and non-root `parent.join(name)` reconstructs the path;
- ancestor checks use strict segment prefixes rather than textual prefixes;
- exact UTF-8 byte limits succeed and one-byte-over inputs fail.

### `MemoryWorkspaceStateMachine`

**Model:** directory set, file-byte mapping, revision, and tiny coherent
`WorkspaceLimits`.

**Rules:** mkdir, guarded write, append, remove, copy, move, simple text patch, bounded
read/stat/list, and one deliberately invalid or over-quota mutation.

**Invariants:**

- recursive public tree and file bytes equal the model after every rule;
- total bytes and node count equal modeled accounting;
- each successful mutation advances revision exactly once;
- unchanged `missing_ok` and every failed mutation preserve tree, counters, hashes, and
  revision;
- file, total-byte, node, path, and segment limits are never exceeded;
- listing is lexical and entry hashes/sizes match modeled bytes;
- copy duplicates exactly the source subtree and move removes exactly that subtree;
- twin workspaces receiving equivalent successful transitions export equal state.

### `WorkspaceSnapshotStateMachine`

**Model:** live workspace projection, immutable exported checkpoints, and a fresh twin
workspace.

**Rules:** export, repeat export, mutate after export, restore an older checkpoint,
restore into a fresh workspace, prepare/commit restore, and submit corrupt,
incompatible, stale-metadata, or oversized data.

**Invariants:**

- unchanged exports are byte-identical;
- equivalent trees have equal canonical encoded state regardless of mutation history;
- restore reinstates bytes, hashes, counters, and saved revision;
- source, restored workspace, and checkpoint bytes remain independent;
- invalid restore preserves the full live projection;
- prepared restore has no effect before commit and cannot be committed to another
  workspace.

### `SnapshotStoreStateMachine`

**Model:** logical UTC clock, live drafts with absolute expiry, deleted/expired
references, count, and payload bytes.

**Rules:** save, duplicate save, load, delete, logical clock advance, purge, and exact
count/byte-capacity admission.

**Invariants:**

- public stats equal the modeled live set;
- quota accounting uses `len(payload)` and is atomic;
- expiration occurs exactly when `expires_at <= now`;
- save and stats sweep all expired records before admission/reporting;
- load removes the queried record when that record is expired but does not promise a
  global expiry sweep;
- delete and repeated purge are idempotent;
- purge returns sorted references and exact removed bytes;
- two saves competing for one final slot produce one success.

### `CommandGrammarStateMachine`

**Model:** bounded word fragments, stages, pipelines, units/connectors, optional final
redirection, approved environment, and cwd.

**Rules:** add bare/single-quoted/double-quoted/escaped/empty/expanding fragments,
complete words and stages, add `|`, `;`, `&&`, add final `>`/`>>`, finalize, or insert one
minimal unsupported/malformed construct.

**Invariants:**

- parsed public `ExecutionPlan` equals the grammar model;
- pipeline precedence is higher than sequencing;
- quoting preserves arguments without shell word splitting or glob expansion;
- single quotes suppress expansion; bare/double fragments expand approved variables;
- `$PWD` derives from cwd;
- unsupported or incomplete syntax fails before dispatch.

Private operator-token classes are not inspected. Word-only tokenization may assert
public `CommandWord`; operator behavior is asserted through `ExecutionPlan`.

### `PipelineExecutorStateMachine`

**Model:** fixed deterministic virtual commands, dispatch trace, cwd/environment,
redirected bytes, bounded limits, and twin workspaces/executors.

**Rules:** standalone execution, one-to-bounded-stage pipelines, `;`/`&&`, replace/append
redirection, unknown or inadmissible commands, and exact/one-over command, stage,
argument, stdout, intermediate, and aggregate limits.

**Invariants:**

- each admitted next stage receives complete prior stdout with stdin connected;
- all stages preflight before any dispatch when admission fails;
- the rightmost non-zero stage determines pipeline status and only final-stage stdout is
  returned;
- stderr remains in stage order;
- intermediate/aggregate overflow starts no later stage and never redirects;
- redirection mutates exactly one modeled file atomically;
- failed destination resolution, quota, timeout, or output overflow preserves the target;
- twin execution from equal snapshots yields equal normalized results and workspace
  exports, excluding measured duration.

## Direct properties

Use `@given` properties rather than a state machine for pure codecs:

- `SessionSnapshotState -> encode -> persisted snapshot -> decode` round trip;
- equal logical state produces equal canonical payload/state hash;
- random identifier and creation time do not change state hash;
- one-byte corruption, incompatible version, invalid cwd/environment, and size overflow
  reject before mutation.

## Shrinking-friendly strategies

### Paths

- Build segments from safe ASCII plus directed multibyte samples `é`, `中`, and `😀`.
- Generate exact `limit - 1`, `limit`, and `limit + 1` UTF-8 byte cases directly.
- Sample invalid categories explicitly: empty, outside root, sibling prefix
  (`/workspace2`), `..`, NUL, unpaired surrogate, and over-limit segment/path.
- Treat backslash as an ordinary filename character; do not normalize it as a separator.

### Bytes, text, and quotas

- General binary payloads are 0-16 bytes with directed empty, NUL, multibyte UTF-8, and
  invalid UTF-8 examples.
- Text excludes surrogate code points and remains at most 16 code points.
- Construct exact and one-over file, aggregate, node, read, patch, and snapshot limits
  from current modeled capacity rather than using `assume`.
- Keep workspace limits coherent: file 1-8 bytes, total file-to-16 bytes, nodes 2-8.

### Command grammar and pipelines

- Build words from bounded atoms and explicit quote/escape/variable forms.
- Generate environment names structurally from shell-variable grammar.
- Cap plans at 3 units, 4 stages per unit, and 5 words per stage.
- Sample unsupported syntax from explicit minimal templates: incomplete operators or
  quotes, duplicate/intermediate redirects, `||`, `<`, `<<`, `&`, comments, backticks,
  command/process substitution, newlines, and invalid variable names.
- Construct stage/intermediate/aggregate sizes at `limit - 1`, `limit`, and `limit + 1`.
- Generate destinations as existing file, absent file, directory, missing parent,
  outside-root path, and one-byte-over-quota target.

## Layer ownership

The property layer owns path algebra, workspace state/accounting, snapshot round trips
and store expiry/capacity, parser grammar, pipeline sequencing/limits, and deterministic
twin execution.

[Direct conformance](../conformance/README.md) owns cross-module lifecycle, policy,
events, secrets, in-place session restore, service publication, resume/fork identity,
cleanup, timeout/cancellation, and host-fallback absence. Focused unit/integration tests
retain exact error-message, single-regression, and scheduler-interleaving cases.

## Implemented layout

```text
tests/
  conftest.py
  property/
    README.md
    strategies.py
    async_machine.py
    workspace/
      test_paths_stateful.py
      test_mutations_stateful.py
      test_snapshots_stateful.py
    snapshots/
      test_store_stateful.py
      test_session_codec_properties.py
    command_executor/
      test_parser_stateful.py
      test_pipeline_stateful.py
      test_deterministic_execution.py
```

## Hypothesis profiles

`tests/conftest.py` will register:

| Profile | Examples | Stateful steps | Database | Purpose |
|---|---:|---:|---|---|
| `local` | 50 | 25 | Enabled | Normal developer feedback and shrinking |
| `ci` | 100 | 30 | Disabled with `derandomize=True` | Reproducible required matrix |
| `extended` | 250 | 50 | Enabled | Manual or scheduled deeper exploration |

All profiles use `deadline=None` and `print_blob=True`. Do not suppress Hypothesis health
checks initially; reduce model bounds or improve strategies if a machine is too slow.

CI sets `HYPOTHESIS_PROFILE=ci` for pytest on Windows and Ubuntu with Python 3.12 and
3.14. The implementation adds a compatible Hypothesis 6.x release to the development
dependency group and regenerates `uv.lock`; no runtime dependency is added.

## Acceptance

- Every machine is bounded and deterministic under the CI profile.
- Failures shrink to a reproducible sequence without custom random loops.
- No generated test reads production-private state or invokes host resources.
- Exact-boundary success and one-over atomic failure are represented in every relevant
  strategy.
- `python -m pytest tests/property -q` passes under local and CI profiles.
- Existing fixed regressions remain focused and are not replaced wholesale.
