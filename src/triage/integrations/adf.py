"""Markdown to Atlassian Document Format.

Jira Cloud's REST v3 API takes issue descriptions and comments as ADF, a JSON
document tree, not as text. Triage composes ticket bodies in markdown — which is
what Slack and `make run-fixture` want — so something has to translate.

This covers the subset the compose prompt is allowed to produce: headings,
paragraphs, bullet and ordered lists, fenced code blocks, and the inline marks
bold, italic, code and link. Anything outside that subset degrades to literal
text rather than being dropped, because a ticket that renders imperfectly is
recoverable and a ticket missing a sentence is not.

Known limits, all deliberate: marks do not nest (bold inside a link renders as
one or the other), and nested lists are flattened to one level.
"""

import re
from typing import Any

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$")
_BULLET = re.compile(r"^\s*[-*+]\s+(.*)$")
_ORDERED = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_FENCE = re.compile(r"^\s*```\s*([\w+-]*)\s*$")

# Ordered: code spans are literal so they win, links before bold so a bold
# label inside a link is not mistaken for emphasis, bold before italic so `**`
# is never read as two italics.
_INLINE = re.compile(
    r"(?P<code>`(?P<code_text>[^`\n]+)`)"
    r"|(?P<link>\[(?P<link_text>[^\]\n]+)\]\((?P<link_href>[^)\s]+)\))"
    r"|(?P<bold>\*\*(?P<bold_text>[^*\n]+)\*\*)"
    r"|(?P<italic>(?<![*\w])[*_](?P<italic_text>[^*_\n]+)[*_](?![*\w]))"
    r"|(?P<url>https?://[^\s<>()\[\]]+)"
)

_URL_TRAILING = ".,;:!?'\""

Node = dict[str, Any]


def _text(value: str, mark: Node | None = None) -> Node:
    node: Node = {"type": "text", "text": value}
    if mark is not None:
        node["marks"] = [mark]
    return node


def inline_nodes(text: str) -> list[Node]:
    """Split one line of prose into ADF text nodes, applying inline marks."""
    nodes: list[Node] = []
    cursor = 0

    for match in _INLINE.finditer(text):
        if match.start() > cursor:
            nodes.append(_text(text[cursor : match.start()]))

        if match.group("code"):
            nodes.append(_text(match.group("code_text"), {"type": "code"}))
        elif match.group("link"):
            href = match.group("link_href")
            nodes.append(_text(match.group("link_text"), {"type": "link", "attrs": {"href": href}}))
        elif match.group("bold"):
            nodes.append(_text(match.group("bold_text"), {"type": "strong"}))
        elif match.group("italic"):
            nodes.append(_text(match.group("italic_text"), {"type": "em"}))
        else:
            url = match.group("url").rstrip(_URL_TRAILING)
            nodes.append(_text(url, {"type": "link", "attrs": {"href": url}}))
            # Punctuation trimmed off the URL is prose, not part of the link.
            trailing = match.group("url")[len(url) :]
            if trailing:
                nodes.append(_text(trailing))

        cursor = match.end()

    if cursor < len(text):
        nodes.append(_text(text[cursor:]))

    # ADF rejects a text node with an empty string, and a paragraph with no
    # content at all is valid, so filter rather than substitute.
    return [node for node in nodes if node["text"]]


def _paragraph(text: str) -> Node | None:
    nodes = inline_nodes(text)
    return {"type": "paragraph", "content": nodes} if nodes else None


def _list_item(text: str) -> Node:
    paragraph = _paragraph(text) or {"type": "paragraph", "content": [_text(" ")]}
    return {"type": "listItem", "content": [paragraph]}


def _blocks(markdown: str) -> list[Node]:
    lines = markdown.splitlines()
    blocks: list[Node] = []
    index = 0

    while index < len(lines):
        line = lines[index]

        if not line.strip():
            index += 1
            continue

        fence = _FENCE.match(line)
        if fence:
            language = fence.group(1)
            index += 1
            body: list[str] = []
            while index < len(lines) and not _FENCE.match(lines[index]):
                body.append(lines[index])
                index += 1
            index += 1  # closing fence, or end of input
            node: Node = {"type": "codeBlock", "content": [_text("\n".join(body) or " ")]}
            if language:
                node["attrs"] = {"language": language}
            blocks.append(node)
            continue

        heading = _HEADING.match(line)
        if heading:
            content = inline_nodes(heading.group(2))
            if content:
                blocks.append(
                    {
                        "type": "heading",
                        "attrs": {"level": min(len(heading.group(1)), 6)},
                        "content": content,
                    }
                )
            index += 1
            continue

        for pattern, list_type in ((_BULLET, "bulletList"), (_ORDERED, "orderedList")):
            if pattern.match(line):
                items: list[Node] = []
                while index < len(lines) and (item := pattern.match(lines[index])):
                    items.append(_list_item(item.group(1)))
                    index += 1
                blocks.append({"type": list_type, "content": items})
                break
        else:
            # A paragraph runs until a blank line or the start of another block.
            body = []
            while index < len(lines) and lines[index].strip() and not _starts_block(lines[index]):
                body.append(lines[index].strip())
                index += 1
            paragraph = _paragraph(" ".join(body))
            if paragraph:
                blocks.append(paragraph)

    return blocks


def _starts_block(line: str) -> bool:
    return bool(
        _HEADING.match(line) or _BULLET.match(line) or _ORDERED.match(line) or _FENCE.match(line)
    )


def to_adf(markdown: str) -> Node:
    """Render markdown as an ADF document.

    Always returns a valid document: Jira rejects one with no content, so an
    empty input yields a single empty paragraph rather than an empty tree.
    """
    content = _blocks(markdown)
    return {"type": "doc", "version": 1, "content": content or [{"type": "paragraph"}]}
