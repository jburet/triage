"""Jira Cloud client, over the REST v3 API.

Jira is one of the two systems Triage may write to. There is no Jira MCP server
available here, so this is a plain Python client — the architecture's rule is
"MCP servers when they exist, Python tools otherwise" (ADR-0013).

Two things about the v3 API shape the code. Authentication is HTTP basic with an
Atlassian account email and an API token, not a bearer token. And issue
descriptions and comments are Atlassian Document Format, a JSON tree, not text —
so the markdown ticket body is translated by
:mod:`triage.integrations.adf` on the way out.

The ``client`` parameter exists so tests can supply an ``httpx.MockTransport``.
Payload shaping and error handling are covered by
``tests/integration/test_jira_client.py``; what no test here can cover is
whether a given Jira project accepts the issue type and labels being sent, so
run ``make run-fixture`` against a staging project before turning
``TRIAGE_DRY_RUN`` off.
"""

from collections.abc import Sequence
from typing import Any

import httpx

from triage.integrations.adf import to_adf
from triage.integrations.base import JiraIssue

API_ROOT = "/rest/api/3"


class JiraError(RuntimeError):
    """A Jira request failed. Carries Jira's own explanation, which is the useful part."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"Jira returned {status}: {detail}")
        self.status = status
        self.detail = detail


def _explain(response: httpx.Response) -> str:
    """Jira reports what was actually wrong in ``errorMessages`` and ``errors``.

    The raw body is a wall of JSON that hides those two fields, and the status
    line alone never says which field was rejected.
    """
    try:
        payload = response.json()
    except ValueError:
        return response.text[:500] or "<empty response>"

    if isinstance(payload, dict):
        messages = list(payload.get("errorMessages") or [])
        field_errors = payload.get("errors")
        if isinstance(field_errors, dict):
            messages += [f"{field}: {message}" for field, message in field_errors.items()]
        if messages:
            return "; ".join(str(message) for message in messages)
    return response.text[:500]


class JiraRestClient:
    def __init__(
        self,
        base_url: str,
        email: str,
        api_token: str,
        *,
        issue_type: str = "Task",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("a Jira base URL is required when dry-run is off")
        if not (email and api_token):
            raise ValueError("a Jira account email and API token are required when dry-run is off")

        self._base_url = base_url.rstrip("/")
        self._issue_type = issue_type
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            auth=httpx.BasicAuth(email, api_token),
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.post(path, json=payload)
        if response.is_error:
            raise JiraError(response.status_code, _explain(response))
        if not response.content:
            return {}
        body = response.json()
        return body if isinstance(body, dict) else {}

    def browse_url(self, key: str) -> str:
        return f"{self._base_url}/browse/{key}"

    async def create_issue(
        self, *, project: str, summary: str, body: str, labels: Sequence[str]
    ) -> JiraIssue:
        payload = {
            "fields": {
                "project": {"key": project},
                "summary": summary,
                "description": to_adf(body),
                "issuetype": {"name": self._issue_type},
                "labels": list(labels),
            }
        }
        created = await self._post(f"{API_ROOT}/issue", payload)
        key = str(created["key"])
        # The `self` field is the API URL; a human needs the browse URL, and it
        # is the one that ends up in Slack and in the tickets table.
        return JiraIssue(key=key, url=self.browse_url(key))

    async def add_comment(self, *, key: str, body: str) -> None:
        await self._post(f"{API_ROOT}/issue/{key}/comment", {"body": to_adf(body)})

    async def aclose(self) -> None:
        await self._client.aclose()
