"""F1 end to end, against the captured alert and every fake (M3 Phase 3).

The graph composes both shared sub-graphs, so what these tests are really about
is the seam: that the collection reaches the diagnosis, that the diagnosis
reaches the ticket, that everything F1 says lands in one Slack thread, and that
the signal's status tells the truth about where the run got to.
"""

import pytest

from tests.conftest import (
    a_service_entry,
    a_synthesis,
    build_deps,
    fake_datadog,
    mapped,
    pod_down_alert,
    run_config,
)
from triage.config import Config
from triage.db.repo import InMemoryRepository
from triage.graphs.incident import build_graph, run_incident
from triage.runtime import Deps
from triage.schemas.postmortem import Postmortem
from triage.schemas.signal import SignalStatus
from triage.schemas.ticket import PipelineOutcome


@pytest.fixture
def config(jira_config: Config) -> Config:
    """This module exercises the Jira path, which the release configures off."""
    return jira_config


def a_postmortem() -> Postmortem:
    return Postmortem(
        timeline="00:43 probe failures; 00:43:54 container killed, exit 137; 00:47 replicas 0.",
        what_happened="The platform pod for one tenant restarted three times in four minutes.",
        why_it_happened="The liveness probe is shorter than the startup. Confidence: medium.",
        what_would_have_helped="No APM on this tenant, so no request-level view of the outage.",
    )


def incident_deps(config: Config, **overrides: object) -> Deps:
    repo = overrides.pop("repo", None) or mapped(a_service_entry("plt-hcl-software-uat"))
    return build_deps(
        config,
        repo=repo,  # type: ignore[arg-type]
        datadog=fake_datadog(),
        postmortems=[a_postmortem()],
        **overrides,  # type: ignore[arg-type]
    )


async def run(deps: Deps, **state: object) -> dict:
    base: dict[str, object] = {"alert": pod_down_alert(), "team": "platform"}
    base.update(state)
    return await build_graph().compile().ainvoke(base, config=run_config(deps))  # type: ignore[arg-type]


async def test_an_alert_becomes_a_ticket_announced_in_one_thread(config: Config):
    deps = incident_deps(config)

    result = await run(deps)

    assert result["outcome"] is PipelineOutcome.TICKET_CREATED
    assert deps.jira.created[0].project == "PLAT"
    opening, *replies = deps.slack.messages
    assert opening.channel == "#platform-alerts"
    assert "Investigating" in opening.text
    assert opening.thread_ts is None
    assert replies
    assert all(message.thread_ts == result["thread_ts"] for message in replies)
    assert any(result["ticket_key"] in message.text for message in replies)


async def test_the_opening_notice_says_how_long_it_has_been_firing(config: Config):
    deps = incident_deps(config)

    await run(deps)

    opening = deps.slack.messages[0]
    assert "plt-hcl-software-uat" in opening.text
    assert "has been firing for" in opening.text
    assert "minutes" in opening.text


async def test_the_collection_reaches_the_diagnosis(config: Config):
    deps = incident_deps(config)

    result = await run(deps)

    prompt = deps.llm.calls[-1].prompt
    assert result["collection"].results
    assert result["hypotheses"]
    assert "exit code 137" in str(result["context"])
    assert prompt  # the postmortem sees the diagnosis it is written from


async def test_the_postmortem_is_a_jira_comment_and_a_slack_link(config: Config):
    deps = incident_deps(config)

    result = await run(deps)

    comment = deps.jira.comments[0]
    assert comment.key == result["ticket_key"]
    assert "Post-mortem draft" in comment.body
    assert "exit 137" in comment.body
    link = deps.slack.messages[-1]
    assert link.thread_ts == result["thread_ts"]
    assert "Post-mortem draft added as a comment" in link.text
    assert "00:43 probe failures" not in link.text


async def test_no_ticket_means_no_postmortem(config: Config):
    """A low-confidence diagnosis ends in a Slack notice, and nothing is written up."""
    deps = incident_deps(config, syntheses=[a_synthesis(confidence="low", chosen_hypothesis=None)])

    result = await run(deps)

    assert result["outcome"] is PipelineOutcome.BELOW_THRESHOLD
    assert result.get("ticket_key") is None
    assert deps.jira.comments == []
    assert "postmortem" not in result


async def test_the_signal_ends_ticketed_and_is_readable_from_the_repository(config: Config):
    repo = mapped(a_service_entry("plt-hcl-software-uat"))
    deps = incident_deps(config, repo=repo)

    result = await run(deps)

    stored = repo.signals[result["signal"].signal_id]
    assert stored.status is SignalStatus.TICKETED
    assert stored.monitor_id == 76154596
    assert stored.external_id == result["alert"].event_id


async def test_a_signal_that_produced_no_ticket_is_discarded(config: Config):
    repo = mapped(a_service_entry("plt-hcl-software-uat"))
    deps = incident_deps(
        config, repo=repo, syntheses=[a_synthesis(confidence="low", chosen_hypothesis=None)]
    )

    result = await run(deps)

    assert repo.signals[result["signal"].signal_id].status is SignalStatus.DISCARDED


async def test_an_unhandled_error_leaves_the_signal_failed(config: Config):
    class Broken(InMemoryRepository):
        async def system_map_for_service(self, service):
            raise RuntimeError("the database is gone")

    repo = Broken()
    deps = incident_deps(config, repo=repo)
    signal = (await run(incident_deps(config)))["signal"]

    with pytest.raises(RuntimeError, match="the database is gone"):
        await run_incident({"alert": pod_down_alert(), "team": "platform", "signal": signal}, deps)

    assert repo.signals[signal.signal_id].status is SignalStatus.FAILED
