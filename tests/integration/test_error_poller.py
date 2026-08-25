"""One tick of the hourly code-exception pass (M8 1.2, 1.3, 1.4, 1.7).

Everything here replays ``tests/fixtures/datadog/errors/org_20260825_1h`` — one
real hour of the org, captured 2026-08-25. Its most important property is that
nothing in it is new: fifteen issues occurred and not one of them was first seen
or regressed inside the hour, which is what a typical tick looks like.
"""

from datetime import UTC, datetime, timedelta

import pytest

from tests.conftest import build_deps, fake_error_datadog, run_config
from triage.config import Config
from triage.errors.issues import Novelty
from triage.graphs.error_poller import graph
from triage.integrations.datadog import FakeDatadogClient
from triage.nodes.poll_errors import CATCH_UP_LIMIT, OVERLAP, POLLER_NAME
from triage.runtime import Deps
from triage.schemas.errors import ErrorTrack

CAPTURE_END = datetime(2026, 8, 25, 5, 35, 24, tzinfo=UTC)


@pytest.fixture
def deps(config: Config) -> Deps:
    return build_deps(config, datadog=fake_error_datadog())


async def tick(deps: Deps, now: datetime = CAPTURE_END) -> dict:
    return await graph.ainvoke({"now": now}, config=run_config(deps))


class TestOneCallPerTrack:
    """1.2 — counts and attributes together, one call each."""

    async def test_asks_each_configured_track_exactly_once(self, deps: Deps) -> None:
        await tick(deps)

        queries = deps.datadog.queries_for("error_issues")  # type: ignore[attr-defined]
        assert len(queries) == 2
        assert [query.split()[0] for query in queries] == ["track:trace", "track:logs"]

    async def test_sends_the_configured_persona(self, deps: Deps) -> None:
        await tick(deps)

        assert all(
            "persona:BACKEND" in query
            for query in deps.datadog.queries_for("error_issues")  # type: ignore[attr-defined]
        )

    async def test_reports_what_each_track_returned(self, deps: Deps) -> None:
        state = await tick(deps)

        assert state["issues_seen"] == {ErrorTrack.TRACE: 15, ErrorTrack.LOGS: 0}

    async def test_a_track_datadog_refuses_is_a_stated_failure(self, config: Config) -> None:
        """One broken track must not cost the other one its tick."""
        replay = fake_error_datadog()
        broken = FakeDatadogClient(responses=replay.responses, fail={"error_issues": "boom"})
        state = await tick(build_deps(config, datadog=broken))

        assert state["failures"]
        assert "boom" in state["failures"][0]


class TestTheEnvironmentIsAFilter:
    """1.3 — never returned rather than returned and dropped."""

    async def test_the_watched_environments_go_into_the_query(self, deps: Deps) -> None:
        await tick(deps)

        assert all(
            query.endswith("env:prod")
            for query in deps.datadog.queries_for("error_issues")  # type: ignore[attr-defined]
        )

    async def test_watching_nothing_asks_nothing(self, config: Config) -> None:
        for team in config.teams:
            team.environments = []
        deps = build_deps(config, datadog=fake_error_datadog())

        state = await tick(deps)

        assert deps.datadog.queries_for("error_issues") == []  # type: ignore[attr-defined]
        assert "no environment" in state["failures"][0]


class TestWhatTheTickDecides:
    """1.4, 1.5 and 1.6, through the graph rather than the rule."""

    async def test_the_captured_hour_produces_nothing(self, deps: Deps) -> None:
        state = await tick(deps)

        assert state.get("new", []) == []
        assert state.get("regressed", []) == []
        assert state["unchanged"] == 15

    async def test_a_window_containing_a_regression_finds_it(self, deps: Deps) -> None:
        """Three of the fifteen regressed; the latest was 2026-08-18."""
        state = await tick(deps, now=datetime(2026, 8, 18, 17, tzinfo=UTC))

        assert [issue.service for issue in state["regressed"]] == ["plt-raiffeisen-uat"]
        assert state["new"] == []

    async def test_an_issue_missing_a_source_location_is_skipped_with_that_reason(
        self, config: Config
    ) -> None:
        payload = _without_file_paths()
        deps = build_deps(
            config,
            datadog=FakeDatadogClient(
                responses={"error_issues": {"track:trace": payload, "track:logs": {"data": []}}}
            ),
        )

        state = await tick(deps, now=datetime(2026, 8, 18, 17, tzinfo=UTC))

        assert state["skipped"]
        assert "source location" in state["skipped"][0].reason
        assert state["regressed"] == []


class TestTheWindow:
    """1.7 — the watermark, the overlap, and what a late poller admits to."""

    async def test_the_first_tick_reads_the_configured_lookback(self, deps: Deps) -> None:
        state = await tick(deps)

        lookback = timedelta(minutes=deps.config.errors.lookback_minutes)
        assert state["window"].start == CAPTURE_END - lookback

    async def test_a_later_tick_reads_from_its_watermark_minus_the_overlap(
        self, deps: Deps
    ) -> None:
        watermark = CAPTURE_END - timedelta(minutes=20)
        await deps.repo.set_watermark(POLLER_NAME, watermark)

        state = await tick(deps)

        assert state["window"].start == watermark - OVERLAP

    async def test_the_tick_moves_the_watermark_on(self, deps: Deps) -> None:
        await tick(deps)

        assert await deps.repo.get_watermark(POLLER_NAME) == CAPTURE_END

    async def test_a_poller_down_too_long_replays_only_the_limit(self, deps: Deps) -> None:
        await deps.repo.set_watermark(POLLER_NAME, CAPTURE_END - timedelta(days=2))

        state = await tick(deps)

        assert state["window"].start == CAPTURE_END - CATCH_UP_LIMIT
        assert state["skipped_span"]

    async def test_and_says_in_the_platform_channel_what_it_skipped(self, deps: Deps) -> None:
        await deps.repo.set_watermark(POLLER_NAME, CAPTURE_END - timedelta(days=2))

        await tick(deps)

        posted = deps.slack.messages  # type: ignore[attr-defined]
        assert len(posted) == 1
        assert posted[0].channel == deps.config.platform_channel()
        assert "48 hours" in posted[0].text or "2880 minutes" in posted[0].text

    async def test_a_tick_inside_the_limit_says_nothing(self, deps: Deps) -> None:
        await deps.repo.set_watermark(POLLER_NAME, CAPTURE_END - timedelta(minutes=20))

        state = await tick(deps)

        assert state.get("skipped_span") is None
        assert deps.slack.messages == []  # type: ignore[attr-defined]


def _without_file_paths() -> dict:
    """The captured search, with every source location removed."""
    from tests.conftest import captured_errors

    payload = captured_errors("search_trace")
    for issue in payload["included"]:
        issue["attributes"]["file_path"] = ""
        issue["attributes"]["function_name"] = ""
    return payload


def test_novelty_is_the_only_reason_a_tick_looks_at_an_issue() -> None:
    assert set(Novelty) == {Novelty.NEW, Novelty.REGRESSED}
