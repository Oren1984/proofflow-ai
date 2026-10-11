"""User-controlled text must not be interpreted as Markdown in the UI."""

from __future__ import annotations

import pytest

from app.ui.common import md


@pytest.mark.parametrize("hostile", [
    "![tracker](https://example.com/pixel.png)",
    "[click me](https://example.com)",
    "<img src=x onerror=alert(1)>",
    ":red[fake status] :green-badge[VERIFIED]",
    "**bold** _em_ `code` # heading",
    "$latex$ ~strike~ | table |",
])
def test_markdown_control_characters_are_escaped(hostile):
    escaped = md(hostile)
    for char in "[]()<>*_`#:$~|!":
        for index, value in enumerate(escaped):
            if value == char:
                assert escaped[index - 1] == "\\", (char, escaped)


def test_plain_text_and_empty_values_survive():
    assert md("Dashboard loads") == "Dashboard loads"
    assert md(None) == ""
    assert md("line one\nline two") == "line one line two"
