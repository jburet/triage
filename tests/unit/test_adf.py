"""The markdown to ADF translation.

Jira rejects a malformed document with a 400 that names no field, so the
properties worth pinning are structural: no empty text nodes, a document is
never empty, and nothing in the input is silently dropped.
"""

import pytest

from tests.conftest import a_draft
from triage.integrations.adf import inline_nodes, to_adf


def _texts(node):
    """Every string in the document, in order."""
    if isinstance(node, dict):
        if node.get("type") == "text":
            return [node["text"]]
        return _texts(node.get("content", []))
    return [text for child in node for text in _texts(child)]


def _types(doc):
    return [block["type"] for block in doc["content"]]


def test_headings_become_headings():
    doc = to_adf("## Symptom\n\nPods were OOM-killed.")
    assert _types(doc) == ["heading", "paragraph"]
    assert doc["content"][0]["attrs"]["level"] == 2
    assert _texts(doc["content"][0]) == ["Symptom"]


def test_blank_line_separates_paragraphs():
    doc = to_adf("First claim.\n\nSecond claim.")
    assert _types(doc) == ["paragraph", "paragraph"]


def test_wrapped_lines_join_into_one_paragraph():
    doc = to_adf("p95 rose from 120 ms\nto 1.4 s over ten minutes.")
    assert _types(doc) == ["paragraph"]
    assert _texts(doc) == ["p95 rose from 120 ms to 1.4 s over ten minutes."]


@pytest.mark.parametrize("bullet", ["-", "*", "+"])
def test_bullet_lists(bullet):
    doc = to_adf(f"{bullet} first\n{bullet} second")
    assert _types(doc) == ["bulletList"]
    assert len(doc["content"][0]["content"]) == 2
    assert _texts(doc) == ["first", "second"]


def test_ordered_lists():
    doc = to_adf("1. first\n2. second")
    assert _types(doc) == ["orderedList"]
    assert _texts(doc) == ["first", "second"]


def test_a_list_ends_where_prose_resumes():
    doc = to_adf("- only item\nBack to prose.")
    assert _types(doc) == ["bulletList", "paragraph"]


def test_fenced_code_block_keeps_its_language_and_newlines():
    doc = to_adf("```sql\nSELECT 1;\nSELECT 2;\n```")
    (block,) = doc["content"]
    assert block["type"] == "codeBlock"
    assert block["attrs"] == {"language": "sql"}
    assert _texts(block) == ["SELECT 1;\nSELECT 2;"]


def test_code_block_without_a_language_omits_the_attribute():
    (block,) = to_adf("```\nplain\n```")["content"]
    assert "attrs" not in block


def test_markdown_inside_a_code_block_stays_literal():
    assert _texts(to_adf("```\n## not a heading\n```")) == ["## not a heading"]


@pytest.mark.parametrize(
    ("markdown", "text", "mark"),
    [
        ("**loud**", "loud", {"type": "strong"}),
        ("*quiet*", "quiet", {"type": "em"}),
        ("_quiet_", "quiet", {"type": "em"}),
        ("`code`", "code", {"type": "code"}),
    ],
)
def test_inline_marks(markdown, text, mark):
    (node,) = inline_nodes(markdown)
    assert node["text"] == text
    assert node["marks"] == [mark]


def test_bold_is_not_read_as_two_italics():
    (node,) = inline_nodes("**loud**")
    assert node["marks"] == [{"type": "strong"}]


def test_markdown_links():
    (node,) = inline_nodes("[the dashboard](https://dd.example/x)")
    assert node["text"] == "the dashboard"
    assert node["marks"] == [{"type": "link", "attrs": {"href": "https://dd.example/x"}}]


def test_bare_urls_become_links():
    nodes = inline_nodes("see https://dd.example/x for detail")
    linked = [n for n in nodes if n.get("marks")]
    assert [n["text"] for n in linked] == ["https://dd.example/x"]


def test_trailing_punctuation_is_not_part_of_a_bare_url():
    """A link ending in '.' is a link Jira renders and nobody can click correctly."""
    nodes = inline_nodes("see https://dd.example/x.")
    linked = [n for n in nodes if n.get("marks")]
    assert linked[0]["text"] == "https://dd.example/x"
    assert "".join(n["text"] for n in nodes).endswith("x.")


def test_a_url_inside_a_markdown_link_is_not_matched_twice():
    nodes = inline_nodes("[dash](https://dd.example/x)")
    assert len(nodes) == 1


def test_unsupported_markdown_degrades_to_literal_text():
    """Losing a sentence is unrecoverable; rendering it plainly is not."""
    doc = to_adf("| a | b |\n| - | - |")
    assert _types(doc) == ["paragraph"]
    assert "| a | b |" in "".join(_texts(doc))


def test_no_text_node_is_ever_empty():
    """ADF rejects an empty text node, and the error names no field."""
    doc = to_adf(a_draft().to_markdown())
    assert all(text for text in _texts(doc))


def test_an_empty_document_is_still_valid():
    assert to_adf("   \n\n  ") == {
        "type": "doc",
        "version": 1,
        "content": [{"type": "paragraph"}],
    }


def test_a_composed_ticket_renders_every_section():
    doc = to_adf(a_draft().to_markdown())
    headings = [b for b in doc["content"] if b["type"] == "heading"]
    assert len(headings) == 9
    assert doc["version"] == 1
