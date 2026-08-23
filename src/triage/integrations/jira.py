"""Real Jira client, over MCP.

Jira is reached through an MCP server rather than its REST API, per the
architecture's "MCP servers when they exist" rule.

Tool names are configuration, not constants: Jira MCP servers do not agree on
them, and hard-coding a guess produces a client that fails at the last step of a
production run. Set them to match the server you deploy.

Not exercised by the test suite — there is no live Jira in CI, and asserting
against a mock of an interface we do not control would prove nothing. The tested
path is :class:`~triage.integrations.base.FakeJiraClient`; this class is the
boundary where that guarantee stops. Verify it against a staging Jira project
before turning ``TRIAGE_DRY_RUN`` off.
"""

from collections.abc import Sequence
from typing import Any

from triage.integrations.base import JiraIssue


class McpJiraClient:
    def __init__(
        self,
        url: str,
        token: str,
        *,
        create_tool: str = "jira_create_issue",
        comment_tool: str = "jira_add_comment",
        issue_type: str = "Task",
        timeout: float = 30.0,
    ) -> None:
        if not url:
            raise ValueError("a Jira MCP URL is required when dry-run is off")
        self._url = url
        self._token = token
        self._create_tool = create_tool
        self._comment_tool = comment_tool
        self._issue_type = issue_type
        self._timeout = timeout

    async def _call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One tool call per connection.

        Triage files a handful of tickets a day, so a connection pool would be
        complexity bought for nothing; a fresh session per call also means a
        dropped connection cannot wedge the graph.
        """
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client

        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        http_client = httpx2.AsyncClient(headers=headers, timeout=self._timeout)
        async with Client(streamable_http_client(self._url, http_client=http_client)) as client:
            result = await client.call_tool(tool, arguments, read_timeout_seconds=self._timeout)

        if result.is_error:
            raise RuntimeError(f"Jira MCP tool {tool!r} failed: {result.content}")
        payload = result.structured_content
        if isinstance(payload, dict):
            return payload
        raise RuntimeError(
            f"Jira MCP tool {tool!r} returned no structured content; the configured "
            f"server must return the issue as structured output"
        )

    async def create_issue(
        self, *, project: str, summary: str, body: str, labels: Sequence[str]
    ) -> JiraIssue:
        payload = await self._call(
            self._create_tool,
            {
                "project_key": project,
                "summary": summary,
                "description": body,
                "issue_type": self._issue_type,
                "labels": list(labels),
            },
        )
        key = str(payload["key"])
        return JiraIssue(key=key, url=str(payload.get("url", "")))

    async def add_comment(self, *, key: str, body: str) -> None:
        await self._call(self._comment_tool, {"issue_key": key, "comment": body})
