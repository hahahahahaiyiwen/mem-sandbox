from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from tests.property.strategies import (
    ExecutionPlanCase,
    RenderedWord,
    execution_plan_cases,
    malformed_command_syntax,
    rendered_words,
    unsupported_command_syntax,
)

from mem_sandbox.command_executor import (
    CommandEmpty,
    CommandEnvironment,
    CommandSyntaxInvalid,
    CommandSyntaxUnsupported,
    EnvironmentValue,
    expand_word,
    parse_execution_plan,
)
from mem_sandbox.workspace import SandboxPath

ENVIRONMENT = CommandEnvironment((EnvironmentValue("NAME", "value"),))
CWD = SandboxPath.resolve("/workspace/project")


@given(case=execution_plan_cases())
def test_generated_execution_plans_match_the_public_grammar_model(
    case: ExecutionPlanCase,
) -> None:
    assert parse_execution_plan(case.source) == case.expected


@given(word=rendered_words())
def test_generated_quote_escape_and_expansion_fragments_preserve_one_argv_entry(
    word: RenderedWord,
) -> None:
    plan = parse_execution_plan(f"echo {word.source}")

    assert len(plan.units) == 1
    assert len(plan.units[0].stages) == 1
    assert plan.units[0].stages[0].words[1] == word.expected
    assert expand_word(word.expected, ENVIRONMENT, CWD) == word.expanded


@given(source=st.sampled_from(("$NAME", "$PWD")))
def test_bare_unbraced_expansion_uses_the_approved_environment_or_cwd(source: str) -> None:
    word = parse_execution_plan(f"echo {source}").units[0].stages[0].words[1]
    expected = "value" if source == "$NAME" else CWD.value

    assert expand_word(word, ENVIRONMENT, CWD) == expected


@given(command=unsupported_command_syntax())
def test_generated_unsupported_syntax_is_rejected_explicitly(command: str) -> None:
    with pytest.raises(CommandSyntaxUnsupported):
        parse_execution_plan(command)


@given(command=malformed_command_syntax())
def test_generated_malformed_or_incomplete_syntax_is_rejected_before_dispatch(
    command: str,
) -> None:
    expected = CommandEmpty if not command.strip() else CommandSyntaxInvalid
    with pytest.raises(expected):
        parse_execution_plan(command)
