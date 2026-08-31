"""Shared deterministic text-line semantics."""


def normalize_line_endings(text: str) -> str:
    """Normalize CRLF and CR to LF without treating other characters as separators."""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def split_normalized_lines(text: str) -> tuple[list[str], bool]:
    """Split only on normalized LF and report whether the file ends with a newline."""
    normalized = normalize_line_endings(text)
    if normalized == "":
        return [], False

    had_trailing_newline = normalized.endswith("\n")
    lines = normalized.split("\n")
    if had_trailing_newline:
        lines.pop()
    return lines, had_trailing_newline
