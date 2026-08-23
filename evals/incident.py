"""Scored qualification against the captured incident (M3 Phase 2, ADR-0016).

Deliberately not part of CI: it calls the ``triage`` and ``analysis`` tiers, so
it costs money on every run. Datadog is *not* called — the collection is replayed
from ``tests/fixtures/datadog/``, which is what makes this reproducible at all.
Logs and spans age out of Datadog in about fifteen days, so those files are the
only permanent record of the incident this whole feature was designed against.

    make evals-incident

What is scored is the one judgement the tests cannot make, because it depends on
model output:

- **the class** — the reference alert counts container deletion events, so it is
  `crash_restart`, not `availability`.
- **the top cause** — the liveness probe was shorter than the pod's own startup,
  so an `infra` cause naming the probe should rank first. This is the thing the
  six hand-run calls found, and the bar Bits AI would have had to clear.
- **the trap** — the window contains a `StatefulSet … deployed` event whose
  before/after differ only in `ready_replicas`. A qualification that proposes a
  `deployment` cause has read the title instead of the diff, which is precisely
  the confident, wrong diagnosis ADR-0016 was written to prevent.

The trap is the most valuable line here. Everything else measures whether the
model is good; that one measures whether the reduction did its job.
"""

import asyncio
import json
from dataclasses import asdict, dataclass, field

from tests.conftest import fake_datadog, pod_down_alert

from triage.config import get_config, get_settings
from triage.nodes.collect import classify_alert, collect, follow_up
from triage.nodes.qualify import qualify
from triage.runtime import DEPS_KEY, build_deps
from triage.schemas.collection import AlertClass
from triage.schemas.hypothesis import CauseType

PROBE_WORDS = ("probe", "liveness", "readiness", "startup", "health")


@dataclass
class Result:
    alert_class: str
    class_correct: bool
    collectors_with_data: int
    collectors_empty: list[str]
    follow_up_calls: int
    top_cause: str
    top_cause_type: str
    probe_ranked_first: bool
    fell_for_the_deploy_event: bool
    causes: list[dict[str, object]] = field(default_factory=list)


async def evaluate() -> Result:
    deps = build_deps(get_settings(), get_config())
    # Every model call is real; every Datadog call is the capture.
    deps = type(deps)(**{**deps.__dict__, "datadog": fake_datadog()})
    run = {"configurable": {DEPS_KEY: deps}}

    state: dict[str, object] = {"alert": pod_down_alert(), "service": "plt-hcl-software-uat"}
    state.update(await classify_alert(state, run))  # type: ignore[arg-type]
    state.update(await collect(state, run))  # type: ignore[arg-type]
    while not state.get("followup_done"):
        state.update(await follow_up(state, run))  # type: ignore[arg-type]
    state.update(await qualify(state, run))  # type: ignore[arg-type]

    collection = state["collection"]
    causes = state["qualification"].causes  # type: ignore[attr-defined]
    ranked = sorted(causes, key=lambda cause: cause.rank_score, reverse=True)
    top = ranked[0] if ranked else None

    return Result(
        alert_class=state["classification"].alert_class.value,  # type: ignore[attr-defined]
        class_correct=state["classification"].alert_class  # type: ignore[attr-defined]
        is AlertClass.CRASH_RESTART,
        collectors_with_data=sum(1 for r in collection.results if r.has_data),  # type: ignore[attr-defined]
        collectors_empty=[
            f"{r.collector.value}: {r.status.value}"
            for r in collection.results  # type: ignore[attr-defined]
            if not r.has_data
        ],
        follow_up_calls=collection.followup_calls,  # type: ignore[attr-defined]
        top_cause=top.description if top else "",
        top_cause_type=top.cause_type.value if top else "",
        probe_ranked_first=bool(
            top
            and top.cause_type is CauseType.INFRA
            and any(word in top.description.lower() for word in PROBE_WORDS)
        ),
        fell_for_the_deploy_event=any(cause.cause_type is CauseType.DEPLOYMENT for cause in causes),
        causes=[
            {
                "type": cause.cause_type.value,
                "rank": cause.rank_score,
                "description": cause.description,
            }
            for cause in ranked
        ],
    )


async def main() -> int:
    result = await evaluate()
    print(json.dumps(asdict(result), indent=2))
    print()
    print(f"class {'✓' if result.class_correct else '✗'}  {result.alert_class}")
    print(f"probe cause first {'✓' if result.probe_ranked_first else '✗'}  {result.top_cause}")
    print(
        f"deploy-event trap {'✗ FELL FOR IT' if result.fell_for_the_deploy_event else '✓ avoided'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
