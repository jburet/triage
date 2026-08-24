# syntax=docker/dockerfile:1
#
# The image one analysis runs in (ADR-0009, ADR-0014, plan M7 phase 3.1).
#
# Two things decide what is in here. The Triage source is exactly the
# entrypoint's import closure — no graph, no node, no Jira, Slack or Datadog
# client — which is the "smaller sandbox" ADR-0014 traded the agent loop for,
# and `tests/unit/test_analysis_image.py` reads these COPY lines to hold it.
# The third-party half is the whole lockfile, because splitting one lock into a
# sandbox half and a graph half is a packaging change this milestone does not
# ask for; it costs image size and no capability the entrypoint can reach.
#
# Built from the repository root: docker build -f docker/analysis/Dockerfile .

FROM python:3.12-slim AS deps

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy
RUN pip install --no-cache-dir uv==0.9.7
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


FROM python:3.12-slim

# git, because the entrypoint fetches its own tree: nothing runs before a Job's
# container. ca-certificates, because that fetch is https.
RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=deps /app/.venv /app/.venv

COPY src/triage/__init__.py /app/src/triage/__init__.py
COPY src/triage/analysis /app/src/triage/analysis
COPY src/triage/config.py /app/src/triage/config.py
COPY src/triage/db/__init__.py /app/src/triage/db/__init__.py
COPY src/triage/db/models.py /app/src/triage/db/models.py
COPY src/triage/db/repo.py /app/src/triage/db/repo.py
COPY src/triage/integrations/__init__.py /app/src/triage/integrations/__init__.py
COPY src/triage/integrations/github.py /app/src/triage/integrations/github.py
COPY src/triage/llm.py /app/src/triage/llm.py
COPY src/triage/prompts /app/src/triage/prompts
COPY src/triage/schemas /app/src/triage/schemas

ENV PATH=/app/.venv/bin:$PATH \
    PYTHONPATH=/app/src \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TRIAGE_ANALYSIS_WORKSPACE=/workspace

# Not root: the sandbox is gVisor's job, but a container that has no reason to
# be root should not be one either.
RUN useradd --create-home --uid 10001 analysis \
    && mkdir -p /workspace \
    && chown analysis:analysis /workspace
USER analysis
WORKDIR /workspace

ENTRYPOINT ["python", "-m", "triage.analysis.entrypoint"]
