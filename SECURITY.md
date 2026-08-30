# Security Policy

## Supported versions

Before the first public release, security fixes are applied to the latest commit on
`main`. After releases begin, this document will list supported release lines.

## Reporting a vulnerability

Do not report suspected vulnerabilities in a public issue.

Use GitHub private vulnerability reporting when it is enabled for this repository. Until
then, contact the repository owner through a private contact method listed on their GitHub
profile. Include:

- affected version or commit
- reproduction steps or a minimal proof of concept
- expected and observed behavior
- potential impact
- any suggested mitigation

The maintainers will acknowledge reports on a best-effort basis, investigate before
disclosure, and coordinate fixes and release notes with the reporter when practical.

## Security boundary

MemSandbox is a logical sandbox for supported workspace and command operations. It is not
an operating-system isolation boundary and must not be used to execute arbitrary
untrusted Python or native code in the same process. See the
[high-level design](./docs/HIGH_LEVEL_DESIGN.md) for the complete security model.
