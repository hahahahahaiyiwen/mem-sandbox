"""Official OpenAI provider tests for the OpenAI Agents SDK samples."""

from __future__ import annotations

import pytest
from agents.models.openai_responses import OpenAIResponsesModel
from openai import AsyncOpenAI
from samples.openai_agents_sdk.providers.openai.__main__ import build_parser, main
from samples.openai_agents_sdk.providers.openai.config import OpenAISettings
from samples.openai_agents_sdk.providers.openai.model import create_openai_model
from samples.openai_agents_sdk.scenarios import list_scenarios

_SCENARIO_NAMES = [
    "workspace-edit",
    "document-review",
    "independent-reviewers",
    "incident-triage",
    "config-migration",
    "data-pipeline",
    "policy-recovery",
    "quota-recovery",
    "multi-agent-handoff",
    "snapshot-branching",
]


def _environment() -> dict[str, str]:
    return {
        "OPENAI_API_KEY": "sample-secret-key",
        "OPENAI_MODEL": "sample-model",
    }


def test_settings_load_environment_and_hide_api_key() -> None:
    settings = OpenAISettings.from_environment(_environment())

    assert settings.api_key == "sample-secret-key"
    assert settings.model == "sample-model"
    assert settings.api_key not in repr(settings)


@pytest.mark.parametrize("variable", ["OPENAI_API_KEY", "OPENAI_MODEL"])
def test_settings_reject_missing_or_empty_values(variable: str) -> None:
    missing = _environment()
    missing.pop(variable)
    with pytest.raises(ValueError, match=variable):
        OpenAISettings.from_environment(missing)

    empty = _environment()
    empty[variable] = "   "
    with pytest.raises(ValueError, match=variable):
        OpenAISettings.from_environment(empty)


@pytest.mark.asyncio
async def test_openai_model_composition_pins_official_endpoint_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")
    settings = OpenAISettings.from_environment(_environment())

    client, model = create_openai_model(settings)
    try:
        assert isinstance(client, AsyncOpenAI)
        assert not hasattr(client, "azure_endpoint")
        assert str(client.base_url) == "https://api.openai.com/v1/"
        assert isinstance(model, OpenAIResponsesModel)
    finally:
        await client.close()


def test_registry_and_parser_expose_all_scenarios() -> None:
    assert [scenario.name for scenario in list_scenarios()] == _SCENARIO_NAMES
    assert build_parser().parse_args([]).scenario == "workspace-edit"
    args = build_parser().parse_args(
        [
            "--scenario",
            "incident-triage",
            "--inspect",
            "--inspect-on-failure",
        ]
    )
    assert args.scenario == "incident-triage"
    assert args.inspect is True
    assert args.inspect_on_failure is True


@pytest.mark.asyncio
async def test_list_scenarios_does_not_require_openai_configuration(
    capsys: pytest.CaptureFixture[str],
) -> None:
    await main(["--list-scenarios"])

    output = capsys.readouterr().out
    for name in _SCENARIO_NAMES:
        assert f"{name}:" in output
