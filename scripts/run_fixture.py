"""Run the ticket pipeline on a fixture diagnosis and print what would be sent.

    make run-fixture
    make run-fixture FIXTURE=tests/fixtures/diagnoses/latency_low_confidence.json

Needs no database and no credentials: with ``TRIAGE_DRY_RUN=1`` Jira and Slack
are recording fakes and state is in-memory. It does call the model tiers through
LiteLLM, which is the point — this is the smallest end-to-end exercise of the
real prompts.
"""

import asyncio
import sys
import textwrap
from pathlib import Path

from triage.config import get_config, get_settings
from triage.graphs.ticket_pipeline import graph
from triage.integrations.base import FakeJiraClient, FakeSlackClient
from triage.runtime import DEPS_KEY, build_deps
from triage.schemas import Diagnosis

DEFAULT_FIXTURE = Path("tests/fixtures/diagnoses/oom_payments.json")


def rule(title: str) -> None:
    print(f"\n\033[1m── {title} {'─' * max(0, 66 - len(title))}\033[0m")


def indent(text: str) -> str:
    return textwrap.indent(text.rstrip(), "  ")


async def main(argv: list[str]) -> int:
    fixture = Path(argv[1]) if len(argv) > 1 and argv[1] else DEFAULT_FIXTURE
    if not fixture.exists():
        print(f"no such fixture: {fixture}", file=sys.stderr)
        return 2

    settings = get_settings()
    if not settings.dry_run:
        print("refusing to run: TRIAGE_DRY_RUN is off, this would file a real ticket")
        return 2

    diagnosis = Diagnosis.model_validate_json(fixture.read_text(encoding="utf-8"))
    deps = build_deps(settings, get_config())

    rule(f"input: {fixture.name}")
    print(indent(f"{diagnosis.service} / {diagnosis.team} — {diagnosis.feature.value}"))
    print(indent(f"confidence: {diagnosis.confidence.value}"))
    print(indent(f"symptom: {diagnosis.symptom.description}"))

    state = await graph.ainvoke({"diagnosis": diagnosis}, config={"configurable": {DEPS_KEY: deps}})

    rule("outcome")
    print(indent(f"{state['outcome'].value}"))
    if state.get("ticket_key"):
        print(indent(f"{state['ticket_key']} — {state['ticket_url']}"))
    print(indent(f"compose attempts: {state.get('compose_attempts', 0)}"))

    assert isinstance(deps.jira, FakeJiraClient)
    assert isinstance(deps.slack, FakeSlackClient)

    for issue in deps.jira.created:
        rule(f"jira issue that would be created in {issue.project}")
        print(indent(f"summary: {issue.summary}"))
        print(indent(f"labels:  {', '.join(issue.labels)}"))
        print(indent(issue.body))

    for comment in deps.jira.comments:
        rule(f"jira comment that would be added to {comment.key}")
        print(indent(comment.body))

    for message in deps.slack.messages:
        rule(f"slack message that would be posted to {message.channel}")
        print(indent(message.text))
        if message.attachment:
            print(indent("--- attachment: draft-ticket.md ---"))
            print(indent(message.attachment))

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
