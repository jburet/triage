"""Outbound integrations, as protocols.

Triage writes to exactly two systems — Jira and Slack — and reads from the rest.
Both write-capable clients have a recording fake, and ``TRIAGE_DRY_RUN=1``
selects the fakes, so the whole pipeline can be exercised end to end without a
single side effect. That is the default for local development.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class JiraIssue:
    key: str
    url: str


class JiraClient(Protocol):
    async def create_issue(
        self, *, project: str, summary: str, body: str, labels: Sequence[str]
    ) -> JiraIssue: ...

    async def add_comment(self, *, key: str, body: str) -> None: ...


class SlackClient(Protocol):
    async def post(self, *, channel: str, text: str, attachment: str | None = None) -> str:
        """Post a message. Returns the message timestamp, Slack's thread handle."""
        ...


@dataclass(frozen=True)
class RecordedIssue:
    project: str
    summary: str
    body: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class RecordedComment:
    key: str
    body: str


@dataclass(frozen=True)
class RecordedMessage:
    channel: str
    text: str
    attachment: str | None


@dataclass
class FakeJiraClient:
    """Records what would have been created. Keys are deterministic: PROJ-1, PROJ-2..."""

    project_prefix_counter: dict[str, int] = field(default_factory=dict)
    created: list[RecordedIssue] = field(default_factory=list)
    comments: list[RecordedComment] = field(default_factory=list)

    async def create_issue(
        self, *, project: str, summary: str, body: str, labels: Sequence[str]
    ) -> JiraIssue:
        self.created.append(
            RecordedIssue(project=project, summary=summary, body=body, labels=tuple(labels))
        )
        number = self.project_prefix_counter.get(project, 0) + 1
        self.project_prefix_counter[project] = number
        key = f"{project}-{number}"
        return JiraIssue(key=key, url=f"https://jira.invalid/browse/{key}")

    async def add_comment(self, *, key: str, body: str) -> None:
        self.comments.append(RecordedComment(key=key, body=body))


@dataclass
class FakeSlackClient:
    """Records what would have been posted."""

    messages: list[RecordedMessage] = field(default_factory=list)

    async def post(self, *, channel: str, text: str, attachment: str | None = None) -> str:
        self.messages.append(RecordedMessage(channel=channel, text=text, attachment=attachment))
        return f"{len(self.messages)}.000000"
