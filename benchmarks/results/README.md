# Reference Results

Committed results in this directory are short-duration engineering observations, not
release gates or comparative product claims.

The initial reference is:

- artifact: `2026-09-08-windows-development/reference-v1.json`;
- report: `2026-09-08-windows-development/reference-v1.md`;
- runner: Windows development workstation;
- power configuration: uncontrolled development workstation;
- workload: `empty` and `small_project` profile version 1;
- samples: two warm-ups and five measured in-process samples; two measured fresh
  interpreters for each cold-process case;
- failures: none across 38 cases.

The run was captured from an implementation worktree before commit, so
`working_tree_dirty` is true and the checked-out commit identifies the branch base. The
artifact's `source_tree_hash` identifies the benchmark and runtime Python sources that
were actually measured.

These values can reveal order-of-magnitude behavior and large regressions. They must not
be used to enforce small percentage budgets because background workload, CPU scaling,
antivirus activity, and other workstation variance were not controlled.
