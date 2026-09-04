from __future__ import annotations

from dataclasses import dataclass

from hypothesis import strategies as st

from mem_sandbox.command_executor import (
    CommandStage,
    CommandWord,
    Connector,
    ExecutionPlan,
    PlanUnit,
    RedirectionMode,
    StdoutRedirection,
    WordFragment,
)

SAFE_SEGMENT_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789_-"
SAFE_WORD_ALPHABET = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-*"
TEXT_ALPHABET = "abc XYZ09_-é中😀"


@dataclass(frozen=True, slots=True)
class Utf8Boundary:
    limit: int
    exact: str
    one_over: str


@dataclass(frozen=True, slots=True)
class InvalidPathCase:
    category: str
    value: str


@dataclass(frozen=True, slots=True)
class RenderedWord:
    source: str
    expected: CommandWord
    expanded: str


@dataclass(frozen=True, slots=True)
class ExecutionPlanCase:
    source: str
    expected: ExecutionPlan


@dataclass(frozen=True, slots=True)
class _RenderedFragment:
    source: str
    expected: WordFragment
    expanded: str
    merges_unquoted: bool


def safe_segments(*, min_size: int = 1, max_size: int = 8) -> st.SearchStrategy[str]:
    return st.text(
        alphabet=SAFE_SEGMENT_ALPHABET,
        min_size=min_size,
        max_size=max_size,
    )


def path_segments(
    *,
    min_size: int = 0,
    max_size: int = 4,
) -> st.SearchStrategy[tuple[str, ...]]:
    return st.lists(
        safe_segments(max_size=6),
        min_size=min_size,
        max_size=max_size,
    ).map(tuple)


def utf8_boundaries(
    *,
    min_limit: int = 9,
    max_limit: int = 20,
) -> st.SearchStrategy[Utf8Boundary]:
    return st.integers(min_value=min_limit, max_value=max_limit).map(
        lambda limit: Utf8Boundary(
            limit=limit,
            exact="é" + ("a" * (limit - 2)),
            one_over="é" + ("a" * (limit - 1)),
        )
    )


def invalid_path_cases() -> st.SearchStrategy[InvalidPathCase]:
    return st.sampled_from(
        (
            InvalidPathCase("empty", ""),
            InvalidPathCase("outside-root", "/tmp/file"),
            InvalidPathCase("sibling-prefix", "/workspace2/file"),
            InvalidPathCase("parent-traversal", "/workspace/a/../file"),
            InvalidPathCase("nul", "/workspace/\x00file"),
            InvalidPathCase("unpaired-surrogate", "/workspace/\ud800"),
        )
    )


def binary_payloads(*, max_size: int = 16) -> st.SearchStrategy[bytes]:
    return st.one_of(
        st.sampled_from((b"", b"\x00", b"\xff", "é".encode(), "😀".encode())),
        st.binary(min_size=0, max_size=max_size),
    )


def small_text(*, max_size: int = 16) -> st.SearchStrategy[str]:
    return st.text(alphabet=TEXT_ALPHABET, min_size=0, max_size=max_size)


def environment_names() -> st.SearchStrategy[str]:
    first = st.sampled_from(tuple("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_"))
    rest = st.text(
        alphabet="ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz_0123456789",
        min_size=0,
        max_size=5,
    )
    return st.builds(str.__add__, first, rest)


def environment_maps() -> st.SearchStrategy[dict[str, str]]:
    return st.dictionaries(
        keys=environment_names(),
        values=small_text(max_size=8),
        min_size=0,
        max_size=4,
    )


def workspace_file_maps() -> st.SearchStrategy[dict[str, bytes]]:
    return st.dictionaries(
        keys=safe_segments(max_size=5),
        values=binary_payloads(max_size=8),
        min_size=0,
        max_size=4,
    )


def _rendered_fragments() -> st.SearchStrategy[_RenderedFragment]:
    bare = st.text(
        alphabet=SAFE_WORD_ALPHABET,
        min_size=1,
        max_size=6,
    ).map(
        lambda text: _RenderedFragment(
            text,
            WordFragment(text, True),
            text,
            True,
        )
    )
    single = st.text(
        alphabet="abc XYZ09_$*é",
        min_size=0,
        max_size=6,
    ).map(
        lambda text: _RenderedFragment(
            f"'{text}'",
            WordFragment(text, False),
            text,
            False,
        )
    )
    double = st.text(
        alphabet="abc XYZ09_*é",
        min_size=0,
        max_size=6,
    ).map(
        lambda text: _RenderedFragment(
            f'"{text}"',
            WordFragment(text, True),
            text,
            False,
        )
    )
    escaped = st.sampled_from((" ", "$", ";", "*", "\\", '"', "'")).map(
        lambda character: _RenderedFragment(
            f"\\{character}",
            WordFragment(character, False),
            character,
            False,
        )
    )
    expansion = st.sampled_from(
        (
            _RenderedFragment("${NAME}", WordFragment("${NAME}", True), "value", True),
            _RenderedFragment('"$NAME"', WordFragment("$NAME", True), "value", False),
            _RenderedFragment("'$NAME'", WordFragment("$NAME", False), "$NAME", False),
            _RenderedFragment(
                "${PWD}",
                WordFragment("${PWD}", True),
                "/workspace/project",
                True,
            ),
        )
    )
    return st.one_of(bare, single, double, escaped, expansion)


@st.composite
def rendered_words(draw: st.DrawFn) -> RenderedWord:
    fragments = draw(st.lists(_rendered_fragments(), min_size=1, max_size=3))
    expected: list[WordFragment] = []
    previous_merges = False
    for fragment in fragments:
        if previous_merges and fragment.merges_unquoted:
            previous = expected[-1]
            expected[-1] = WordFragment(
                previous.text + fragment.expected.text,
                True,
            )
        else:
            expected.append(fragment.expected)
        previous_merges = fragment.merges_unquoted
    return RenderedWord(
        source="".join(fragment.source for fragment in fragments),
        expected=CommandWord(tuple(expected)),
        expanded="".join(fragment.expanded for fragment in fragments),
    )


@st.composite
def execution_plan_cases(draw: st.DrawFn) -> ExecutionPlanCase:
    unit_count = draw(st.integers(min_value=1, max_value=3))
    rendered_units: list[str] = []
    expected_units: list[PlanUnit] = []
    connectors: list[Connector] = [Connector.ALWAYS]
    connectors.extend(
        draw(
            st.lists(
                st.sampled_from((Connector.ALWAYS, Connector.ON_SUCCESS)),
                min_size=unit_count - 1,
                max_size=unit_count - 1,
            )
        )
    )

    for unit_index in range(unit_count):
        stage_count = draw(st.integers(min_value=1, max_value=4))
        rendered_stages: list[str] = []
        expected_stages: list[CommandStage] = []
        for _ in range(stage_count):
            words = draw(st.lists(rendered_words(), min_size=1, max_size=5))
            rendered_stages.append(" ".join(word.source for word in words))
            expected_stages.append(CommandStage(tuple(word.expected for word in words)))

        redirection: StdoutRedirection | None = None
        rendered_unit = " | ".join(rendered_stages)
        if draw(st.booleans()):
            destination = draw(rendered_words())
            mode = draw(st.sampled_from((RedirectionMode.REPLACE, RedirectionMode.APPEND)))
            operator = ">" if mode is RedirectionMode.REPLACE else ">>"
            rendered_unit = f"{rendered_unit} {operator} {destination.source}"
            redirection = StdoutRedirection(mode, destination.expected)

        rendered_units.append(rendered_unit)
        expected_units.append(
            PlanUnit(
                connector=connectors[unit_index],
                stages=tuple(expected_stages),
                redirection=redirection,
            )
        )

    source = rendered_units[0]
    for index in range(1, unit_count):
        separator = ";" if connectors[index] is Connector.ALWAYS else "&&"
        source = f"{source} {separator} {rendered_units[index]}"
    return ExecutionPlanCase(source, ExecutionPlan(tuple(expected_units)))


def unsupported_command_syntax() -> st.SearchStrategy[str]:
    return st.sampled_from(
        (
            "echo a || echo b",
            "cat < file",
            "cat << EOF",
            "echo a &",
            "echo $(pwd)",
            "echo <(pwd)",
            "echo `pwd`",
            "echo a\npwd",
            "echo a # comment",
            "echo hi 2> err",
            "echo hi 2>> err",
        )
    )


def malformed_command_syntax() -> st.SearchStrategy[str]:
    return st.sampled_from(
        (
            "",
            " ",
            "echo '",
            "echo \\",
            "echo &&",
            "; echo",
            "echo >",
            "echo > a b",
            "echo > a > b",
            "echo first > out | cat",
            "echo first |",
            "| cat",
            "echo ${é}",
            "echo $é",
        )
    )
