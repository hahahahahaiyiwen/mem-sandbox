# Session module

`mem_sandbox.session` owns the public `SandboxSession` facade, its requests/results,
lifecycle state, stable errors, and the narrow collaborator ports it consumes.

All public operations are serialized by one session-owned gate and use one end-to-end
deadline. The session borrows behavior collaborators, closes only its injected
`SessionResourceScope`, and keeps path resolution, workspace mutation, command execution,
policy, event delivery, secrets, and snapshot persistence behind constructor-injected
interfaces.

The public host-facing surface includes portable archive export and validate-then-publish
restore. Restore may require an expected current workspace revision and root hash;
mismatches raise `SessionWorkspaceChanged` while the operation gate prevents a stale
publication from overwriting newer session work.

The [artifact-workflow audit](../../../docs/product-validation/artifact-workflow-audit.md)
links these guarantees to a host input/output recipe and the distinction between
pre-publication failure and error reporting after a committed mutation. The
[bounded artifact-exchange design](../../../docs/product-validation/artifact-exchange-design.md)
keeps future artifact facades host-facing, deadline-bound, and coordinated through
session-owned admission and workspace-owned publication.
