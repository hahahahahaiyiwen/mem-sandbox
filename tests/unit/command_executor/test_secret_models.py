from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from mem_sandbox.command_executor import (
    CommandContext,
    CommandEnvironment,
    CommandEnvironmentOverlay,
    CommandEnvironmentOverlayEntry,
    CommandEnvironmentView,
    CommandExecutionContext,
    CommandLimits,
    EnvironmentValue,
    ExecuteRequest,
    NoOpCommandValueProtection,
)
from mem_sandbox.workspace import SandboxPath

CANARY = "secret-\N{LOCK}-canary"


class OverlayValue:
    def __init__(self, value: str) -> None:
        self._value = value
        self.calls = 0

    def reveal_text(self) -> str:
        self.calls += 1
        return self._value


def test_overlay_and_view_are_immutable_non_revealing_and_overlay_first() -> None:
    protected = OverlayValue(CANARY)
    overlay = CommandEnvironmentOverlay(
        (
            CommandEnvironmentOverlayEntry("TOKEN", protected),
            CommandEnvironmentOverlayEntry("A", OverlayValue("overlay")),
        )
    )
    view = CommandEnvironmentView(
        CommandEnvironment(
            (
                EnvironmentValue("A", "base"),
                EnvironmentValue("BASE", "visible"),
            )
        ),
        overlay,
    )

    assert view.get("A") == "overlay"
    assert view.get("TOKEN") == CANARY
    assert view.get("BASE") == "visible"
    assert view.get("MISSING", "default") == "default"
    assert [(item.name, item.value) for item in view.values] == [
        ("A", "overlay"),
        ("BASE", "visible"),
        ("TOKEN", CANARY),
    ]
    assert view.is_overlay_name("TOKEN")
    assert not view.is_overlay_name("BASE")
    assert protected.calls == 2
    for value in (protected, overlay, view, *overlay.entries, *view.values):
        assert CANARY not in repr(value)
        assert CANARY not in str(value)
    with pytest.raises(FrozenInstanceError):
        overlay.entries = ()  # type: ignore[misc]


def test_overlay_rejects_invalid_duplicate_and_pwd_names() -> None:
    value = OverlayValue(CANARY)
    with pytest.raises(ValueError, match="PWD"):
        CommandEnvironmentOverlayEntry("PWD", value)
    with pytest.raises(ValueError, match="duplicate"):
        CommandEnvironmentOverlay(
            (
                CommandEnvironmentOverlayEntry("TOKEN", value),
                CommandEnvironmentOverlayEntry("TOKEN", value),
            )
        )
    with pytest.raises(TypeError, match="tuple"):
        CommandEnvironmentOverlay([])  # type: ignore[arg-type]


def test_command_limits_include_a_positive_secret_binding_bound() -> None:
    assert CommandLimits().max_secret_bindings == 16
    assert CommandLimits(max_secret_bindings=1).max_secret_bindings == 1
    with pytest.raises(ValueError, match="positive"):
        CommandLimits(max_secret_bindings=0)

    overlay = CommandEnvironmentOverlay(
        (
            CommandEnvironmentOverlayEntry("A", OverlayValue("one")),
            CommandEnvironmentOverlayEntry("B", OverlayValue("two")),
        )
    )
    with pytest.raises(ValueError, match="max_secret_bindings"):
        ExecuteRequest(
            "env",
            SandboxPath.root(),
            CommandEnvironment(),
            CommandLimits(max_secret_bindings=1),
            overlay,
        )


def test_command_context_preserves_the_legacy_no_secret_constructor() -> None:
    environment = CommandEnvironment((EnvironmentValue("NAME", "value"),))

    context = CommandContext(
        SandboxPath.root(),
        environment,
        None,
        None,
        None,
    )
    equivalent_context = CommandContext(
        SandboxPath.root(),
        environment,
        None,
        None,
        None,
    )

    assert isinstance(context.environment, CommandEnvironmentView)
    assert context.environment.get("NAME") == "value"
    assert isinstance(context.protection, NoOpCommandValueProtection)
    assert NoOpCommandValueProtection() == NoOpCommandValueProtection()
    assert hash(NoOpCommandValueProtection()) == hash(NoOpCommandValueProtection())
    assert CommandExecutionContext() == CommandExecutionContext()
    assert hash(CommandExecutionContext()) == hash(CommandExecutionContext())
    assert context == equivalent_context
    assert hash(context) == hash(equivalent_context)
