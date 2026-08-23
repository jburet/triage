# Timings

One line per plan phase and per tool run. Append, never rewrite. Keep them going down.

| date | plan / phase | command or scope | seconds |
|---|---|---|---|
| 2026-08-23 | datadog credentials template | make lint | 1 |
| 2026-08-23 | datadog credentials template | make test | 3 |
| 2026-08-23 | M2 phase 1.1 | pytest tests/unit/test_analysis_runner.py | 2 |
| 2026-08-23 | M2 phase 1.1 | make lint | 19 |
| 2026-08-23 | M2 phase 1.1 | make test | 2 |
| 2026-08-23 | M2 phase 1.2 | pytest tests/unit/test_local_analysis_runner.py | 2 |
| 2026-08-23 | M2 phase 1.2 | make lint + make test | 3 |
| 2026-08-23 | M2 phase 1.3 | pytest tests/unit/test_kubernetes_job_runner.py | 1 |
| 2026-08-23 | M2 phase 1.3 | make lint + make test | 3 |
| 2026-08-23 | M2 phase 2.1-2.2 | pytest tests/unit/test_analysis_context.py | 1 |
| 2026-08-23 | M2 phase 2.1-2.2 | pytest tests/unit/test_analysis_entrypoint.py | 1 |
| 2026-08-23 | M2 phase 2.1-2.2 | pytest tests/unit/test_summaries.py | 1 |
| 2026-08-23 | M2 phase 2.1-2.2 | make lint | 1 |
| 2026-08-23 | M2 phase 2.1-2.2 | make test (247 passed) | 2 |
| 2026-08-23 | M2 phase 2.3 | make lint | 1 |
| 2026-08-23 | M2 phase 2.3 | make test (247 passed) | 1 |
| 2026-08-23 | datadog collection experiment | explore_datadog: 8 collection calls | 4 |
| 2026-08-23 | datadog collection experiment | full exploration session (org scan + 1 incident) | ~600 |
| 2026-08-23 | M2 phase 3.1-3.4 | pytest tests/unit/test_system_map_repo.py | 3 |
| 2026-08-23 | M2 phase 3.1-3.4 | pytest tests/integration/test_cartography.py | 1 |
| 2026-08-23 | M2 phase 3 | make lint | 1 |
| 2026-08-23 | M2 phase 3 | make test (276 passed) | 2 |
| 2026-08-23 | M2 phase 4.1 | pytest tests/unit/test_invalidation.py | 1 |
| 2026-08-23 | M2 phase 4.1 | pytest tests/integration/test_github_client.py | 1 |
| 2026-08-23 | M2 phase 4.1 | pytest tests/unit/test_system_map_repo.py | 1 |
| 2026-08-23 | M2 phase 4.1-4.3 | pytest tests/integration/test_cartography.py | 1 |
| 2026-08-23 | M2 phase 4 | run_cartography --merge (dry run, no spend) | 2 |
| 2026-08-23 | M2 phase 4 | make lint | 1 |
| 2026-08-23 | M2 phase 4 | make test (304 passed) | 2 |
| 2026-08-23 | ADR 0016-0018 + doc consistency | writing and cross-linking | 300 |
| 2026-08-23 | M3 plan rewrite (phases 2 and 4) | writing | 240 |
| 2026-08-23 | promote capture script to scripts/ | write, smoke-test, regenerate fixtures | 420 |
| 2026-08-23 | promote capture script to scripts/ | make lint + make test | 7 |
| 2026-08-23 | M3 phase 1 | pytest tests/integration/test_analysis.py | 4 |
| 2026-08-23 | M3 phase 1 | make lint | 12 |
| 2026-08-23 | M3 phase 1 | make test (315 passed) | 10 |
| 2026-08-23 | M3 phase 2 | pytest tests/unit/test_collection.py | 1 |
| 2026-08-23 | M3 phase 2 | pytest tests/integration/test_collect.py | 1 |
| 2026-08-23 | M3 phase 2 | make lint | 2 |
| 2026-08-23 | M3 phase 2 | make test (341 passed) | 2 |
| 2026-08-23 | M3 phase 3 | pytest tests/integration/test_incident.py | 1 |
| 2026-08-23 | M3 phase 3 | make lint | 2 |
| 2026-08-23 | M3 phase 3 | make test (361 passed) | 3 |
| 2026-08-23 | M3 phase 4 | pytest tests/integration/test_poller.py | 1 |
| 2026-08-23 | M3 phase 4 | make lint | 2 |
| 2026-08-23 | M3 phase 4 | make test (374 passed) | 2 |
| 2026-08-23 | M3 one-shot harness | scripts/run_incident against captured fixtures (offline smoke) | 2 |
| 2026-08-23 | M3 one-shot | run_incident --collect-only on a live alert (12 Datadog calls) | 2 |
| 2026-08-23 | M3 scoping fixes | make test (376 passed) | 2 |
| 2026-08-23 | M3 one-shot | run_incident on the pod-down alert (9 Datadog calls) | 1 |
| 2026-08-23 | M3 scope fixes | make test (379 passed) | 2 |
| 2026-08-23 | direct Anthropic client | make lint + make test (389 passed) | 6 |
| 2026-08-23 | local LiteLLM | docker compose pull + first start (image ~1 GB) | 16 |
| 2026-08-23 | local LiteLLM | make proxy, cold: db + 151 prisma migrations + health | 20 |
| 2026-08-23 | local LiteLLM | make lint + make test (389 passed) | 8 |
