# Controlled network test fixtures

This directory contains a public, test-only certificate and private key for
`example.test`. They have no production trust or confidentiality value and exist only
to exercise TLS SNI and hostname verification against an explicitly controlled
loopback server. Tests install this trust only through a monkeypatched private context
factory; the production transport exposes no custom trust-root seam.

Do not reuse the key outside tests, add real credentials, or weaken client verification
to make a fixture pass. Regenerate the pair only when its algorithm, validity interval,
or `example.test` subject no longer satisfies cross-platform test requirements.
