# Session module

`mem_sandbox.session` owns the public `SandboxSession` facade, its requests/results,
lifecycle state, stable errors, and the narrow collaborator ports it consumes.

All public operations are serialized by one session-owned gate and use one end-to-end
deadline. The session borrows behavior collaborators, closes only its injected
`SessionResourceScope`, and keeps path resolution, workspace mutation, command execution,
policy, event delivery, secrets, and snapshot persistence behind constructor-injected
interfaces.

Milestone 3 exposes execute, bounded text read, text write, patch, host binary/stat/list,
and snapshot create/restore. It does not implement service ownership, framework adapters,
secret-bearing public requests, policy obligations, or best-effort event delivery.
