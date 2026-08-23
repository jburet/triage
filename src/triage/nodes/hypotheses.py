"""Which hypotheses earn an analysis, and which are recorded instead (ADR-0005).

A rule, not a model call: the ranking already happened in ``qualify``, and an
analysis costs a sandboxed Job and an ``analysis``-tier call, so what is worth
deciding here is only how many of them to buy. The floor and the cap are
``config.yaml``'s, and the one hard rule is that a run always analyses
*something* — a set where nothing clears the floor is a weak set, not an empty
one, and returning no diagnosis at all is the least useful outcome available.
"""

from langchain_core.runnables import RunnableConfig

from triage.graphs.state import AnalysisState, Deferred
from triage.runtime import deps_from_runnable_config


async def select_hypotheses(
    state: AnalysisState, config: RunnableConfig | None = None
) -> AnalysisState:
    deps = deps_from_runnable_config(config)
    tuning = deps.config.analysis
    hypotheses = state.get("hypotheses", [])
    if not hypotheses:
        return {"selected": [], "deferred": []}

    order = sorted(
        range(len(hypotheses)), key=lambda index: hypotheses[index].rank_score, reverse=True
    )
    clearing = [index for index in order if hypotheses[index].rank_score >= tuning.min_rank_score]
    chosen = (clearing or order[:1])[: tuning.max_hypotheses]
    kept = set(chosen)

    deferred = [
        Deferred(
            hypothesis=hypotheses[index],
            reason=(
                f"Ranked {hypotheses[index].rank_score:.2f}, below the "
                f"{tuning.min_rank_score:.2f} floor for analysis."
                if hypotheses[index].rank_score < tuning.min_rank_score
                else f"Ranked {hypotheses[index].rank_score:.2f}: outside the "
                f"{tuning.max_hypotheses} most plausible causes, which were analysed instead."
            ),
        )
        for index in order
        if index not in kept
    ]
    return {"selected": [hypotheses[index] for index in chosen], "deferred": deferred}
