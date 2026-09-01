# Snapshots module

`mem_sandbox.snapshots` owns the immutable session snapshot envelope, deterministic
version-1 JSON codec, stable snapshot errors, and process-local in-memory store.

The codec hashes only restorable state: the opaque workspace snapshot, cwd, approved
environment, session schema version, and capability profile version. Snapshot identity,
creation time, and configured payload limits remain outside that deterministic state.

The Milestone 3 store preserves source-session provenance and immutable opaque references.
It intentionally does not implement owner authorization, expiration, count quotas,
store-wide byte quotas, or schema migrations.
