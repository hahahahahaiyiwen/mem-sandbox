# OSS POSIX and Bash Parser Evaluation

**Status:** Decision recorded
**Decision date:** 2026-09-02
**Issue:** [#16](https://github.com/hahahahahaiyiwen/mem-sandbox/issues/16)

## Question

Should MemSandbox replace its constrained command parser with an existing OSS POSIX or
Bash parser?

MemSandbox is not a complete shell, but supported commands should behave like their
familiar POSIX/GNU/Bash counterparts. The parser must therefore recognize common command
syntax accurately while continuing to reject unsupported behavior before any command or
workspace mutation begins.

## Requirements

The parser boundary must:

- run on Python 3.12 through 3.14 on Windows and Linux;
- be compatible with the project's MIT distribution;
- parse but never execute host commands;
- preserve single-quoted, double-quoted, and unquoted word fragments;
- represent `;`, `&&`, `|`, `>`, and `>>` structurally;
- identify unsupported substitutions, redirections, background jobs, heredocs, and
  compound shell constructs;
- reject malformed or unsupported input before dispatch;
- produce immutable values that can be validated against a per-executor command registry
  and capability profile;
- avoid moving command-language ownership into `SandboxService` or `SandboxSession`.

## Current MemSandbox parser

The current implementation in `src/mem_sandbox/command_executor/parser.py` is a
dependency-free tokenizer, constrained parser, and environment-expansion implementation.

It already provides:

- explicit `WordFragment` values preserving whether environment expansion is permitted;
- adjacent mixed quoted and unquoted fragments;
- deterministic handling of the approved escape rules;
- structural parsing for `;`, `&&`, `>`, and `>>`;
- explicit rejection of unsupported syntax such as command substitution, process
  substitution, input redirection, heredocs, background jobs, and multiline input;
- parsing of the complete request before command dispatch;
- stable domain errors instead of host-parser exceptions.

Issue #16 extends the model to immutable `CommandStage`, `PlanUnit`, and `ExecutionPlan`
values and adds `|`. The parser remains syntax-only; registry resolution, environment
expansion, pipeline admission, and effective limits remain executor-owned.

The primary maintenance risk is that a hand-written tokenizer must continue recognizing
enough shell syntax to reject unsupported constructs correctly as the accepted grammar
grows.

## Candidates

### Python `shlex`

[Python documentation](https://docs.python.org/3/library/shlex.html)

`shlex` is stable, dependency-free, and PSF-licensed. It is useful when only a flattened
argument list is required.

It is not a replacement for the MemSandbox parser because it:

- does not produce a command AST or pipeline/list structure;
- removes quote characters and does not retain per-fragment quote provenance;
- does not model command or parameter substitution;
- would still require a separate structural parser and source-provenance layer.

Using `shlex` before another parser would create two tokenization authorities without
removing the custom logic MemSandbox needs.

### Parsify

[micepram/parsify](https://github.com/micepram/parsify)

Parsify is a compact MIT-licensed, character-based POSIX lexer. It is useful as a readable
reference for POSIX token-recognition rules, but it is not suitable as a dependency or
fork base.

The evaluated implementation:

- is a lexer rather than a structural parser;
- joins quoted and unquoted content into one word value and discards quote provenance;
- provides no source spans;
- does not represent command, arithmetic, or process substitution as complete
  constructs;
- does not consume heredoc bodies;
- is not published as the evaluated project on PyPI and has no tagged release;
- would require architectural changes to produce the values MemSandbox already owns.

Adapting it would result in another bespoke parser while starting from a less expressive
word model than the current `WordFragment` implementation.

### `bashlex`

[idank/bashlex](https://github.com/idank/bashlex)

`bashlex` produces a useful Bash-oriented AST and retains source positions, but it is
derived from GNU Bash and distributed under GPLv3+. That creates an avoidable licensing
and distribution risk for an MIT-licensed library. Its release and compatibility signals
are also weaker than the alternatives.

MemSandbox will not depend on `bashlex`.

### Tree-sitter with `tree-sitter-bash`

- [tree-sitter-bash](https://github.com/tree-sitter/tree-sitter-bash)
- [tree-sitter Python package](https://pypi.org/project/tree-sitter/)
- [tree-sitter-bash Python package](https://pypi.org/project/tree-sitter-bash/)

This is the only dependency candidate that materially exceeds the current parser's
capabilities.

Advantages:

- MIT licensing;
- actively maintained Bash grammar;
- complete concrete syntax tree with source byte ranges;
- distinct nodes for single-quoted, double-quoted, unquoted, and concatenated words;
- structural nodes for pipelines, lists, redirects, substitutions, compound commands,
  and heredocs;
- parse-error nodes that can be rejected as a complete request;
- published Windows and Linux wheels for supported Python versions.

Costs and risks:

- two runtime packages and native extension wheels;
- version pinning and CST compatibility maintenance;
- a full Bash grammar that accepts much more than MemSandbox supports;
- a mandatory default-deny CST walk and translation layer;
- future Python or platform wheel availability risk;
- package and installation cost that does not implement command behavior such as `grep`,
  `find`, `cp`, or `wc`.

If adopted, MemSandbox must reject any parse containing an error node and reject every CST
node type that is not explicitly allowed. The CST would be translated into
MemSandbox-owned immutable plan values before registry or execution logic runs.

### Other evaluated approaches

`shasta` requires either GPL-licensed `libbash` for parsing or an external parser
process. `mvdan/sh` is a capable BSD-licensed Go library but has no supported in-process
Python binding. General parser libraries such as Lark or pyparsing would still require
MemSandbox to author and maintain a shell grammar.

None is preferable to the current parser for the approved constrained language.

## Comparison

| Option | Structural tree | Quote provenance | License fit | Windows/Linux | Dependency cost | Decision |
|---|---|---|---|---|---|---|
| Current parser | Constrained immutable plan | Native `WordFragment` state | Yes | Yes | None | Continue |
| `shlex` | No | No | Yes | Yes | None | Do not use as primary parser |
| Parsify | Tokens only | No | Yes | Likely | Vendoring/fork | Reference only |
| `bashlex` | Bash AST | Source offsets | No/uncertain for project goals | Yes | Pure Python | Reject |
| `tree-sitter-bash` | Full Bash CST | First-class nodes and spans | Yes | Yes | Two native-wheel packages | Candidate for a measured spike |

## Decision

Continue issue #16 with the current MemSandbox parser and approved constrained grammar.
Do not add an OSS parser dependency now.

This decision is based on:

1. The accepted grammar remains small and explicit.
2. The existing parser already preserves the quote and expansion distinctions required
   by MemSandbox.
3. Most agent compatibility work belongs to command handlers and result semantics, not
   shell parsing.
4. A full Bash parser still requires a substantial default-deny translator.
5. Avoiding a native dependency keeps installation and Python/platform support simple.

## Re-evaluation trigger

Run a focused `tree-sitter-bash` spike before expanding the grammar beyond the approved
`word`, `;`, `&&`, `|`, `>`, and `>>` subset, or when maintaining explicit rejection of
unsupported constructs becomes larger or riskier than translating a full CST.

The spike must demonstrate:

- unambiguous translation of every accepted syntax case;
- default-deny rejection of every unsupported node family;
- rejection of every parse containing error nodes;
- preservation of source and quote provenance;
- Python 3.12 and 3.14 support on Windows and Ubuntu;
- acceptable wheel availability, installation size, and cold-import cost;
- lower implementation and maintenance complexity than the constrained parser.

Only then should the parser dependency decision be reopened.

## Ownership if an OSS parser is adopted

The grammar or language object may be shared by a session factory, but parsing and CST
translation remain inside `mem_sandbox.command_executor`.

`SandboxService` owns lifecycle and construction. It does not parse command text.
`SandboxSession` coordinates policy and operation ordering. It does not reinterpret shell
syntax. Policy consumes an immutable prepared-plan summary exposed by the
command-executor boundary rather than reparsing the request.

## Maintenance rule

Changes to the accepted command grammar, parser dependency, or default-deny translation
strategy require updates to this evaluation, the command-executor README, the high-level
design, and the issue acceptance matrix.
