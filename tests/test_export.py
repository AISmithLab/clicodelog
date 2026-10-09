"""Markdown export structure: content can never break out of its block."""

import re

from clicodelog.routes.export import _build_markdown, _closed, _fence

FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")


def _balanced(md: str) -> bool:
    """CommonMark fences all close, and <details> balance outside code."""
    open_f, details = None, 0
    for line in md.split("\n"):
        m = FENCE.match(line)
        if open_f is None:
            if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                open_f = m.group(1)
            elif line.startswith("<details>"):
                details += 1
            elif line == "</details>":
                details -= 1
        elif m and m.group(1)[0] == open_f[0] and len(m.group(1)) >= len(open_f) \
                and not m.group(2).strip():
            open_f = None
    return open_f is None and details == 0


def test_fence_outgrows_any_backtick_run():
    assert _fence("plain") == "```"
    assert _fence("a\n```\nb") == "````"
    assert _fence("x ````` y") == "``````"


def test_closed_adds_a_closer_only_when_needed():
    assert _closed("text") == "text"
    assert _closed("```py\nx\n```") == "```py\nx\n```"
    assert _closed("```py\nx") == "```py\nx\n```"
    assert _closed("````\n```\nstill open") == "````\n```\nstill open\n````"
    assert _closed("~~~\nx") == "~~~\nx\n~~~"
    assert _closed("```py\n     ```") == "```py\n     ```\n```", "4+ spaces do not close"
    assert _closed("``` inline ` code") == "``` inline ` code", "not a fence"


def test_markdown_inside_tool_output_and_cut_off_replies_stays_contained():
    conv = {"messages": [
        {"role": "user", "content": "read the README"},
        {"role": "assistant", "content": "Reading.", "thinking": "plan:\n```\nunterminated",
         "tool_uses": [{"name": "read_file", "input": {"path": "README.md"},
                        "result": "# Title\n```bash\nmake\n```\n</details>\n"}]},
        {"role": "assistant", "content": "Cut off at the length limit:\n```python\ndef f("},
        {"role": "user", "content": "next question"},
    ]}
    md, _, _ = _build_markdown("s", "/p", conv)
    assert _balanced(md)
    for text in ("make", "def f(", "next question", "unterminated"):
        assert text in md
    # The last turn is outside any code block, so it renders as a turn.
    assert md.rstrip().endswith("next question")
