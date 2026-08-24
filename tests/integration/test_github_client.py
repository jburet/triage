"""The GitHub compare client, against a mock transport.

The one call Triage makes to GitHub is read-only and answers one question: what
changed between the commit the map reflects and the commit that was merged. The
request is fully determined by our code, so the path, the auth header and the
truncation handling are worth pinning down; whether a real repository is
reachable with the configured token is what a staging run is for.
"""

from datetime import UTC, datetime

import httpx
import pytest

from triage.integrations.github import COMPARE_FILE_CAP, GitHubError, GitHubRestClient

API = "https://api.github.com"


def client_replying(requests, *, status=200, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=body if body is not None else {"files": []})

    inner = httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(handler))
    return GitHubRestClient("token-123", api_url=API, client=inner)


async def test_it_compares_the_two_commits_and_returns_the_changed_filenames():
    requests: list[httpx.Request] = []
    client = client_replying(
        requests,
        body={"files": [{"filename": "src/payments/api.py"}, {"filename": "README.md"}]},
    )

    paths = await client.changed_paths(
        "github.com/org/payments-api", base="1111111", head="9f2c1ab"
    )

    assert paths == ["src/payments/api.py", "README.md"]
    assert requests[0].url.path == "/repos/org/payments-api/compare/1111111...9f2c1ab"


async def test_it_authenticates_with_the_token_and_pins_the_api_version():
    requests: list[httpx.Request] = []

    await client_replying(requests).changed_paths("github.com/org/x", base="a", head="b")

    assert requests[0].headers["authorization"] == "Bearer token-123"
    assert requests[0].headers["x-github-api-version"]


@pytest.mark.parametrize(
    "url",
    [
        "github.com/org/payments-api",
        "https://github.com/org/payments-api",
        "git@github.com:org/payments-api.git",
        "https://github.com/org/payments-api.git",
    ],
)
async def test_every_spelling_of_a_repository_url_reaches_the_same_endpoint(url):
    requests: list[httpx.Request] = []

    await client_replying(requests).changed_paths(url, base="a", head="b")

    assert requests[0].url.path == "/repos/org/payments-api/compare/a...b"


async def test_a_url_that_names_no_repository_is_refused_before_any_request():
    requests: list[httpx.Request] = []

    with pytest.raises(GitHubError, match="not a GitHub repository"):
        await client_replying(requests).changed_paths("example.invalid/x", base="a", head="b")

    assert requests == []


async def test_a_refused_comparison_raises_with_what_github_said():
    requests: list[httpx.Request] = []
    client = client_replying(requests, status=404, body={"message": "Not Found"})

    with pytest.raises(GitHubError, match="Not Found"):
        await client.changed_paths("github.com/org/x", base="a", head="b")


async def test_a_comparison_at_githubs_file_cap_is_refused_rather_than_read_as_the_whole_diff():
    """GitHub caps the list at 300 files and does not say it did so. A capped list
    taken as the whole diff would let a merge that changed everything look like one
    that changed nothing readable."""
    requests: list[httpx.Request] = []
    capped = [{"filename": f"docs/{index}.md"} for index in range(COMPARE_FILE_CAP)]
    client = client_replying(requests, body={"files": capped})

    with pytest.raises(GitHubError, match="cap"):
        await client.changed_paths("github.com/org/x", base="a", head="b")


LIGHTWEIGHT = {"object": {"type": "commit", "sha": "9f2c1ab"}}
ANNOTATED = {"object": {"type": "tag", "sha": "7a7a7a7"}}


def client_routing(routes, requests):
    """A client whose answer depends on the path, for reads that take two requests."""

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status, body = routes.get(request.url.path, (404, {"message": "Not Found"}))
        return httpx.Response(status, json=body)

    inner = httpx.AsyncClient(base_url=API, transport=httpx.MockTransport(handler))
    return GitHubRestClient("token-123", api_url=API, client=inner)


async def test_a_lightweight_tag_resolves_to_the_commit_it_names():
    requests: list[httpx.Request] = []
    client = client_routing({"/repos/org/x/git/ref/tags/501": (200, LIGHTWEIGHT)}, requests)

    assert await client.commit_for_tag("github.com/org/x", "501") == "9f2c1ab"
    assert [request.url.path for request in requests] == ["/repos/org/x/git/ref/tags/501"]


async def test_an_annotated_tag_resolves_to_the_commit_and_not_to_the_tag_object():
    """The ref points at the tag object; its sha is a ref no clone can check out."""
    requests: list[httpx.Request] = []
    client = client_routing(
        {
            "/repos/org/x/git/ref/tags/v2.0": (200, ANNOTATED),
            "/repos/org/x/git/tags/7a7a7a7": (
                200,
                {"object": {"type": "commit", "sha": "9f2c1ab"}},
            ),
        },
        requests,
    )

    assert await client.commit_for_tag("github.com/org/x", "v2.0") == "9f2c1ab"
    assert [request.url.path for request in requests][-1] == "/repos/org/x/git/tags/7a7a7a7"


async def test_a_tag_the_repository_does_not_have_is_an_answer_rather_than_an_error():
    requests: list[httpx.Request] = []

    assert await client_routing({}, requests).commit_for_tag("github.com/org/x", "501") is None


async def test_a_refused_tag_read_raises_with_what_github_said():
    requests: list[httpx.Request] = []
    client = client_routing(
        {"/repos/org/x/git/ref/tags/501": (403, {"message": "API rate limit exceeded"})}, requests
    )

    with pytest.raises(GitHubError, match="rate limit"):
        await client.commit_for_tag("github.com/org/x", "501")


async def test_the_default_branch_commit_is_read_off_the_branch_the_repository_names():
    requests: list[httpx.Request] = []
    client = client_routing(
        {
            "/repos/org/x": (200, {"default_branch": "develop"}),
            "/repos/org/x/commits": (200, [{"sha": "9f2c1ab"}]),
        },
        requests,
    )

    assert await client.default_branch_commit("github.com/org/x") == "9f2c1ab"
    assert requests[-1].url.params["sha"] == "develop"
    assert "until" not in requests[-1].url.params


async def test_a_branch_with_no_commit_is_refused_rather_than_answered_empty():
    requests: list[httpx.Request] = []
    client = client_routing(
        {"/repos/org/x": (200, {"default_branch": "main"}), "/repos/org/x/commits": (200, [])},
        requests,
    )

    with pytest.raises(GitHubError, match="no commit"):
        await client.default_branch_commit("github.com/org/x")


async def test_the_default_branch_is_read_as_it_stood_at_the_time_it_is_asked_about():
    """Thursday's `main` is a different repository from Tuesday's, and an outage
    diagnosed against the wrong one reads real code that never ran."""
    requests: list[httpx.Request] = []
    client = client_routing(
        {
            "/repos/org/x": (200, {"default_branch": "main"}),
            "/repos/org/x/commits": (200, [{"sha": "9f2c1ab"}]),
        },
        requests,
    )

    await client.default_branch_commit("github.com/org/x", at=datetime(2026, 8, 22, tzinfo=UTC))

    assert requests[-1].url.params["until"] == "2026-08-22T00:00:00+00:00"
