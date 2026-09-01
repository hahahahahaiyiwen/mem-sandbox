# Command executor module

This package owns the deterministic constrained command language. It parses a complete
immutable plan before dispatch, resolves only an immutable case-sensitive registry, and
never invokes a host shell or executable.

## Boundary and invariants

- The exact default profile is `pwd`, `cd`, `ls`, `cat`, `echo`, `mkdir`, `touch`, and
  `rm`; aliases are explicit and absent by default.
- Command handlers receive only constructor-injected narrow workspace ports.
- I/O and orchestration are async-first. Command requests always carry explicit stdin;
  first-wave dispatch uses `""`, and every descriptor rejects non-empty stdin.
- Cwd and environment changes are immutable return values. Workspace mutations are
  committed by individual workspace calls.
- The complete plan has one timeout. Cooperative request cancellation is checked before
  and after each handler boundary and returns no transient state. A monotonic deadline
  check prevents suppressed cancellation from returning success or starting later
  commands. Native `asyncio` task cancellation propagates unchanged.
- Stdout and stderr are independently UTF-8 bounded. Redirection never writes a truncated
  stdout value.
- Environment values and command result streams are valid UTF-8 domain text; invalid
  surrogate-containing values are rejected at model construction.

## Maintenance

Keep tokenization separate from parsing and execution. Any grammar, command metadata,
state-transition, output-limit, cancellation, or redirection change requires matching
tests and an update to `docs/components/command-executor/README.md`.
