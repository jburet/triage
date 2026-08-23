"""The Jira REST client, against a mock transport.

The MCP client this replaced could not be tested at all — asserting against a
mock of a protocol we did not control would have proved nothing. A REST client
is different: the request Triage sends is fully determined by our code, so the
payload shape, the auth header, the ADF translation and the error handling are
all worth pinning down here.

What remains untestable is whether a real project accepts the issue type and
labels. That is what a staging run is for.
"""

import base64
import json

import httpx
import pytest

from tests.conftest import a_draft
from triage.integrations.jira import JiraError, JiraRestClient

BASE = "https://acme.atlassian.net"


def client_recording(requests, *, status=201, body=None):
    """A JiraRestClient whose transport records requests and replies canned JSON."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=body if body is not None else {"key": "PAY-1"})

    transport = httpx.MockTransport(handler)
    inner = httpx.AsyncClient(
        base_url=BASE,
        transport=transport,
        auth=httpx.BasicAuth("sre@acme.example", "token-123"),
        headers={"Accept": "application/json"},
    )
    return JiraRestClient(BASE, "sre@acme.example", "token-123", client=inner)


def payload_of(request: httpx.Request) -> dict:
    return json.loads(request.content)


async def test_create_issue_posts_to_the_v3_endpoint():
    requests: list[httpx.Request] = []
    jira = client_recording(requests)

    await jira.create_issue(project="PAY", summary="s", body="body", labels=["triage"])

    (request,) = requests
    assert request.method == "POST"
    assert request.url.path == "/rest/api/3/issue"


async def test_create_issue_sends_the_expected_fields():
    requests: list[httpx.Request] = []
    jira = client_recording(requests)

    await jira.create_issue(
        project="PAY",
        summary="payments-api OOM-killed 11 times",
        body="## Symptom\n\nPods were OOM-killed.",
        labels=["triage", "triage-f1"],
    )

    fields = payload_of(requests[0])["fields"]
    assert fields["project"] == {"key": "PAY"}
    assert fields["summary"] == "payments-api OOM-killed 11 times"
    assert fields["issuetype"] == {"name": "Task"}
    assert fields["labels"] == ["triage", "triage-f1"]


async def test_the_description_is_adf_not_markdown():
    """v3 rejects a plain string here, and the error does not say so."""
    requests: list[httpx.Request] = []
    jira = client_recording(requests)

    await jira.create_issue(
        project="PAY", summary="s", body="## Symptom\n\nPods were OOM-killed.", labels=[]
    )

    description = payload_of(requests[0])["fields"]["description"]
    assert description["type"] == "doc"
    assert description["version"] == 1
    assert description["content"][0]["type"] == "heading"


async def test_a_composed_ticket_survives_the_round_trip():
    requests: list[httpx.Request] = []
    jira = client_recording(requests)
    draft = a_draft()

    await jira.create_issue(
        project="PAY", summary=draft.summary, body=draft.to_markdown(), labels=[]
    )

    description = payload_of(requests[0])["fields"]["description"]
    headings = [b for b in description["content"] if b["type"] == "heading"]
    assert len(headings) == 9


async def test_the_returned_url_is_the_browse_url_a_human_can_open():
    jira = client_recording([], body={"key": "PAY-42", "self": f"{BASE}/rest/api/3/issue/10042"})
    issue = await jira.create_issue(project="PAY", summary="s", body="b", labels=[])

    assert issue.key == "PAY-42"
    assert issue.url == f"{BASE}/browse/PAY-42"


async def test_authentication_is_basic_with_email_and_token():
    requests: list[httpx.Request] = []
    jira = client_recording(requests)

    await jira.create_issue(project="PAY", summary="s", body="b", labels=[])

    header = requests[0].headers["authorization"]
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.removeprefix("Basic ")).decode()
    assert decoded == "sre@acme.example:token-123"


async def test_add_comment_targets_the_issue_and_sends_adf():
    requests: list[httpx.Request] = []
    jira = client_recording(requests)

    await jira.add_comment(key="PAY-7", body="Recurrence #3.")

    (request,) = requests
    assert request.url.path == "/rest/api/3/issue/PAY-7/comment"
    assert payload_of(request)["body"]["type"] == "doc"


async def test_jira_field_errors_are_surfaced_not_buried():
    """Jira explains the real problem in `errors`; the status line never does."""
    jira = client_recording(
        [],
        status=400,
        body={"errorMessages": [], "errors": {"labels": "Label cannot contain spaces."}},
    )

    with pytest.raises(JiraError, match=r"labels: Label cannot contain spaces\."):
        await jira.create_issue(project="PAY", summary="s", body="b", labels=["bad label"])


async def test_jira_error_messages_are_surfaced():
    jira = client_recording([], status=404, body={"errorMessages": ["Issue does not exist."]})

    with pytest.raises(JiraError, match=r"Issue does not exist\.") as caught:
        await jira.add_comment(key="PAY-999", body="hello")
    assert caught.value.status == 404


async def test_a_non_json_error_body_still_raises_usefully():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="upstream gateway error")

    inner = httpx.AsyncClient(base_url=BASE, transport=httpx.MockTransport(handler))
    jira = JiraRestClient(BASE, "a@b.c", "token", client=inner)

    with pytest.raises(JiraError, match="upstream gateway error"):
        await jira.create_issue(project="PAY", summary="s", body="b", labels=[])


@pytest.mark.parametrize(
    ("base_url", "email", "token"),
    [("", "a@b.c", "t"), (BASE, "", "t"), (BASE, "a@b.c", "")],
)
def test_incomplete_credentials_fail_at_construction(base_url, email, token):
    """Better here than on the first real incident."""
    with pytest.raises(ValueError, match="required when dry-run is off"):
        JiraRestClient(base_url, email, token)


def test_a_trailing_slash_on_the_base_url_does_not_double_up():
    jira = JiraRestClient(f"{BASE}/", "a@b.c", "token")
    assert jira.browse_url("PAY-1") == f"{BASE}/browse/PAY-1"
