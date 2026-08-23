"""The GitHub compare client, against a mock transport.

The one call Triage makes to GitHub is read-only and answers one question: what
changed between the commit the map reflects and the commit that was merged. The
request is fully determined by our code, so the path, the auth header and the
truncation handling are worth pinning down; whether a real repository is
reachable with the configured token is what a staging run is for.
"""

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
