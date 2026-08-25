"""The Platform client, against a mock transport (plan M7 phase 5.2).

No Platform has ever been deployed, so what can honestly be pinned down here is
the half our code decides: the path, the body and the header of each request, and
that a refusal is raised with what the Platform said rather than swallowed.
Whether a real Platform accepts these is what a first deployment is for.
"""

import httpx
import pytest
import yaml

from tests.conftest import REPO_ROOT
from triage.integrations.platform import PlatformRestClient

PLATFORM = "https://triage-platform.internal"
CRON_FILE = REPO_ROOT / "deploy" / "platform" / "cron-alert-poller.yaml"


def client_replying(requests, *, status=200, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(status, json=body if body is not None else {})

    inner = httpx.AsyncClient(
        base_url=PLATFORM,
        headers={"Content-Type": "application/json", "x-api-key": "key-123"},
        transport=httpx.MockTransport(handler),
    )
    return PlatformRestClient(PLATFORM, "key-123", client=inner)


async def test_a_run_is_created_on_the_thread_the_poller_named():
    requests: list[httpx.Request] = []
    client = client_replying(requests)

    run = await client.create_run(
        assistant_id="incident", thread_id="incident-42", payload={"service": "payments-api"}
    )

    assert run.thread_id == "incident-42"
    assert requests[0].url.path == "/threads/incident-42/runs"
    assert requests[0].headers["x-api-key"] == "key-123"


async def test_the_cron_is_created_stateless_with_its_schedule_and_input():
    requests: list[httpx.Request] = []
    client = client_replying(requests, body={"cron_id": "cron-1"})

    cron = await client.create_cron(
        assistant_id="alert_poller", schedule="* * * * *", metadata={"owner": "triage"}
    )

    assert cron.cron_id == "cron-1"
    assert requests[0].url.path == "/runs/crons"
    body = yaml.safe_load(requests[0].content.decode())
    assert body == {
        "assistant_id": "alert_poller",
        "schedule": "* * * * *",
        "input": {},
        "metadata": {"owner": "triage"},
    }


async def test_the_crons_already_scheduled_are_read_before_one_is_added():
    """Creating twice is two ticks a minute, so applying has to be able to see."""
    requests: list[httpx.Request] = []
    client = client_replying(
        requests,
        body=[{"cron_id": "cron-1", "assistant_id": "alert_poller", "schedule": "* * * * *"}],
    )

    crons = await client.crons("alert_poller")

    assert [cron.cron_id for cron in crons] == ["cron-1"]
    assert requests[0].url.path == "/runs/crons/search"


async def test_a_platform_that_refuses_says_so_with_what_it_said():
    client = client_replying([], status=422, body={"detail": "no assistant alert_poller"})

    with pytest.raises(RuntimeError, match="no assistant alert_poller"):
        await client.create_cron(assistant_id="alert_poller", schedule="* * * * *")


def test_the_cron_object_names_a_graph_the_platform_serves():
    """A cron for an assistant `langgraph.json` does not register never fires."""
    cron = yaml.safe_load(CRON_FILE.read_text(encoding="utf-8"))
    registered = yaml.safe_load((REPO_ROOT / "langgraph.json").read_text(encoding="utf-8"))[
        "graphs"
    ]

    assert cron["assistant_id"] in registered
    assert cron["schedule"] == "* * * * *", "ADR-0017 polls every 60 seconds"
