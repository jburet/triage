"""Scored fixture suite: does the pipeline produce tickets a developer can use?

Deliberately not part of CI. It calls the real model tiers, so it costs money on
every run, and its result is a score to watch over time rather than a pass/fail
gate. Run it when a prompt or a schema changes.

    make evals

The metric that matters is **first-time-right**: the share of fixtures that
reached a ticket on the first compose, with no self-review retry. Retries are not
free — they are latency and spend — so a rising retry rate is a prompt
regression even when every ticket eventually passes.
"""

import asyncio
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from triage.config import get_config, get_settings
from triage.graphs.ticket_pipeline import graph
from triage.runtime import DEPS_KEY, build_deps
from triage.schemas import Diagnosis, PipelineOutcome, TicketSection

FIXTURE_DIR = Path("tests/fixtures/diagnoses")

# What each fixture is supposed to do. A fixture whose diagnosis is honestly
# below threshold *should* produce no ticket; scoring it as a failure would
# reward the pipeline for over-filing.
EXPECTED_OUTCOME: dict[str, PipelineOutcome] = {
    "oom_payments": PipelineOutcome.TICKET_CREATED,
    "latency_low_confidence": PipelineOutcome.BELOW_THRESHOLD,
}


@dataclass
class Result:
    fixture: str
    expected: str
    actual: str
    correct: bool
    first_time_right: bool
    compose_attempts: int
    empty_sections: list[str]


async def evaluate(path: Path) -> Result:
    diagnosis = Diagnosis.model_validate_json(path.read_text(encoding="utf-8"))
    deps = build_deps(get_settings(), get_config())
    state = await graph.ainvoke({"diagnosis": diagnosis}, config={"configurable": {DEPS_KEY: deps}})

    outcome = state["outcome"]
    attempts = state.get("compose_attempts", 0)
    expected = EXPECTED_OUTCOME.get(path.stem, PipelineOutcome.TICKET_CREATED)

    draft = state.get("draft")
    empty = [s.value for s in TicketSection if not draft.section(s).strip()] if draft else []

    return Result(
        fixture=path.stem,
        expected=expected.value,
        actual=outcome.value,
        correct=outcome is expected,
        first_time_right=outcome is expected and attempts <= 1,
        compose_attempts=attempts,
        empty_sections=empty,
    )


async def main() -> int:
    settings = get_settings()
    if not settings.dry_run:
        print("refusing to run: TRIAGE_DRY_RUN is off, evals would file real tickets")
        return 2

    fixtures = sorted(FIXTURE_DIR.glob("*.json"))
    results = [await evaluate(path) for path in fixtures]

    total = len(results)
    correct = sum(r.correct for r in results)
    ftr = sum(r.first_time_right for r in results)

    print(json.dumps([asdict(r) for r in results], indent=2))
    print(f"\noutcome correct:  {correct}/{total}")
    print(f"first-time-right: {ftr}/{total}")
    for result in results:
        if result.empty_sections:
            print(f"  ! {result.fixture}: empty sections {result.empty_sections}")
    return 0 if correct == total else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
