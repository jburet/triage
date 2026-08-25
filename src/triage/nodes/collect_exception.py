"""F2's collection node: the evidence behind one group, or the absence of it.

No model call anywhere on it. F1 spends a ``triage`` call deciding what class of
failure an alert describes, because the collectors depend on the answer; an error
group already names its exception type, its message and its source location, so
there is nothing left to classify and the queries are arithmetic on fields
Datadog returned (ADR-0016's "every fact comes from a call we made").

What the node adds over :func:`triage.errors.sweep.collect_group` is the window
and the budget: the window runs back from the tick rather than from the group's
``first_seen``, and the whole payload is cut to ``collection.max_prompt_bytes``
with every cut stated (M8 3.4).
"""

from datetime import UTC, datetime

import structlog
from langchain_core.runnables import RunnableConfig

from triage.collect.budget import fit
from triage.errors.sweep import collect_group, collection_window
from triage.graphs.state import CodeExceptionState
from triage.runtime import deps_from_runnable_config

log = structlog.get_logger(__name__)


async def collect_exception(
    state: CodeExceptionState, config: RunnableConfig | None = None
) -> CodeExceptionState:
    deps = deps_from_runnable_config(config)
    group = state["group"]
    window = state.get("window") or collection_window(
        datetime.now(UTC), deps.config.errors.lookback_minutes
    )
    collection = await collect_group(deps.datadog, group, window, deps.config.collection)
    log.info(
        "exception_collected",
        group=group.key,
        evidence=len(collection.evidence),
        gaps=[result.status.value for result in collection.results if not result.has_data],
    )
    return {
        "window": window,
        "collection": fit(collection, deps.config.collection.max_prompt_bytes),
    }
