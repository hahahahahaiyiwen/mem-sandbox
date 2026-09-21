# Session module

`mem_sandbox.session` owns the public `SandboxSession` facade, its requests/results,
lifecycle state, stable errors, and the narrow collaborator ports it consumes.

All public operations are serialized by one session-owned gate and use one end-to-end
deadline. The session borrows behavior collaborators, closes only its injected
`SessionResourceScope`, and keeps path resolution, workspace mutation, command execution,
policy, event delivery, secrets, and snapshot persistence behind constructor-injected
interfaces.

An optional host-selected `OutboundHttpBinding` adds the typed `send_http()` operation.
The default session has no binding and fails before gateway use. A connected session
uses the same operation gate, policy, deadline, cancellation, metadata, and event
sequence as other public operations while URL and transport behavior remain owned by
`mem_sandbox.network`.

The public host-facing surface includes portable archive export and validate-then-publish
restore. Restore may require an expected current workspace revision and root hash;
mismatches raise `SessionWorkspaceChanged` while the operation gate prevents a stale
publication from overwriting newer session work.

The [artifact-workflow audit](../../../docs/product-validation/artifact-workflow-audit.md)
links these guarantees to a host input/output recipe and the distinction between
pre-publication failure and error reporting after a committed mutation. The
[bounded artifact-exchange design](../../../docs/product-validation/artifact-exchange-design.md)
keeps future artifact facades host-facing, deadline-bound, and coordinated through
session-owned admission and workspace-owned publication. The
[authority/accounting design](../../../docs/product-validation/authority-accounting-design.md)
documents the conditional grants, event facts, and settlement paths that any future
session facade must preserve.

Milestone 9A implements only the HTTP grant/gateway seam. Session close cooperatively
cancels an active HTTP operation but never closes the borrowed host-scoped gateway.
Snapshots carry no network authority; service resume supplies the current host profile.
The gateway call runs in a child task so provider self-cancellation is translated as a
stable gateway failure rather than impersonating caller cancellation. Cancellation
settlement tracks the collaborator before its bounded wait; repeated native
cancellation therefore cannot bypass fail-closed retention, and close can re-signal an
unfinished collaborator without waiting indefinitely.
