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
| 2026-08-23 | external proxy addressing | make lint + make test (391 passed) | 5 |
| 2026-08-23 | one-shot | run_incident --prompts on the pod-down alert (no reachable proxy) | 22 |
| 2026-08-23 | one-shot | run_incident full chain on the pod-down alert, real models | 118 |
| 2026-08-23 | proxy fix | make lint + make test (392 passed) | 6 |
| 2026-08-23 | F0 on real repos | run_cartography --local --db, datacatalog + platform-infra | 60 |
| 2026-08-23 | F1 on plt-merck-qa | run_incident --db --local, 3 real analyses | 93 |
| 2026-08-23 | tenant + entrypoint work | make lint + make test (395 passed) | 7 |
| 2026-08-23 | M6 phase 1.1 | make lint + make test (406 passed) | 42 |
| 2026-08-23 | M6 phase 1.2 | make lint + make test (408 passed) | 5 |
| 2026-08-23 | M6 phase 1.3 | make lint + make test (412 passed) | 5 |
| 2026-08-23 | M6 phase 1.4 | make lint + make test (416 passed) | 4 |
| 2026-08-23 | M6 phase 2.1 | make lint + make test (446 passed) | 5 |
| 2026-08-23 | M6 phase 2.2 | make lint + make test (451 passed) | 4 |
| 2026-08-23 | M6 phase 2.3 | make lint + make test (462 passed) | 5 |
| 2026-08-23 | M6 phase 2.4 | make lint + make test (467 passed) | 5 |
| 2026-08-23 | M6 phase 2.5 | make lint + make test (469 passed) | 5 |
| 2026-08-23 | M6 deployed_repo order | make lint + make test (476 passed) | 4 |
| 2026-08-23 | M6 public interface | make lint + make test (476 passed) | 4 |
| 2026-08-23 | M6 one-shot | run_mapping on plt-hcl-software-uat, live Datadog (1 call) | 2 |
| 2026-08-24 | M6 phase 2.6/2.7 | make lint + make test (485 passed) | 5 |
| 2026-08-24 | M6 phase 2.9 | make lint + make test (488 passed) | 6 |
| 2026-08-24 | M6 phase 2.10 | make lint + make test (491 passed) | 5 |
| 2026-08-24 | M6 phase 2.8/2.14 | make lint + make test (495 passed) | 5 |
| 2026-08-24 | M6 phase 2.15 | make lint + make test (500 passed) | 5 |
| 2026-08-24 | M6 phase 2.11 | make lint + make test (505 passed) | 4 |
| 2026-08-24 | M6 phase 2.12 | make lint + make test (507 passed) | 5 |
| 2026-08-24 | M6 phase 2.13 | make lint + make test (508 passed) | 5 |
| 2026-08-24 | M6 phase 2.16 | make lint + make test (517 passed) | 5 |
| 2026-08-24 | M6 phase 3.3 | make lint + make test (519 passed) | 5 |
| 2026-08-24 | M6 phase 3.1 | make lint + make test (534 passed) | 5 |
| 2026-08-24 | M6 phase 3.2 | make lint + make test (540 passed) | 5 |
| 2026-08-24 | M6 phase 3.4 | make lint + make test (546 passed) | 5 |
| 2026-08-24 | M6 phase 4.1 | make lint + make test (550 passed) | 5 |
| 2026-08-24 | M6 phase 4.2 | make lint + make test (553 passed) | 5 |
| 2026-08-24 | M6 phase 4.3 | make lint + make test (560 passed) | 6 |
| 2026-08-24 | M6 phase 4.4 | make lint + make test (567 passed) | 5 |
| 2026-08-24 | M6 phase 4.4 | make run-mapping ARGS="plt-hcl-software-uat" (live Datadog) | 8 |
| 2026-08-24 | M6 close-out | make lint + make test (567 passed) | 4 |
| 2026-08-24 | M6 live GitHub | make run-mapping ARGS="plt-hcl-software-uat" (live Datadog + live GitHub) | 3 |
| 2026-08-24 | ADR-0021 declares | make lint + make test (588 passed) | 12 |
| 2026-08-24 | ADR-0021 proof | make run-mapping ARGS="plt-hcl-software-uat" (live, chart resolved) | 4 |
| 2026-08-24 | F1 live attempt | make run-incident (live Datadog ok; LiteLLM proxy unreachable, killed) | 622 |
| 2026-08-24 | F1 live run | make run-incident (live Datadog + LiteLLM; died at qualify, schema shape) | 43 |
| 2026-08-24 | F1 live re-run | make run-incident (shape correction did not take; nested-list tool args) | 40 |
| 2026-08-24 | qualify shape probe | scratch probes vs live proxy, 30-odd analysis calls (50% arg-shape failure) | 300 |
| 2026-08-24 | ADR-0022 probes | ~80 analysis calls measuring the malformed-tool-call rate | 900 |
| 2026-08-24 | ADR-0022 strict | direct-API probes: 3/8 plain, 6/6 strict, ~40 calls | 420 |
| 2026-08-24 | F1 end to end | make run-incident, direct provider, live Datadog + 4 tier calls | 100 |
| 2026-08-24 | follow_up unwrap | make lint | 1 |
| 2026-08-24 | follow_up unwrap | make test (605) | 3 |
| 2026-08-24 | F1 live re-run | make run-incident, proxy provider, died at diagnosis on an unknown model name | 40 |
| 2026-08-24 | follow_up compose | make lint + make test (606) | 4 |
| 2026-08-24 | model-name check | make lint + make test (611) | 4 |
| 2026-08-24 | F1 end to end | make run-incident, proxy provider, 4 tier calls, no retries | 64 |
| 2026-08-24 | M7 phase 1.1 | make lint + make test (614 passed) | 6 |
| 2026-08-24 | M7 phase 1.2 | make lint + make test (616 passed) | 6 |
| 2026-08-24 | M7 phase 1.3 | make lint + make test (617 passed) | 5 |
| 2026-08-24 | M7 phase 1.4 | make lint + make test (618 passed) | 5 |
| 2026-08-24 | M7 phase 1.5 | make lint + make test (618 passed) | 5 |
| 2026-08-24 | M7 phase 2.1 | make lint + make test (620 passed) | 5 |
| 2026-08-24 | M7 phase 2.2 | make lint + make test (621 passed) | 5 |
| 2026-08-24 | M7 phase 2.3 | make lint + make test (623 passed) | 5 |
| 2026-08-24 | M7 phase 2.4 | make lint + make test (625 passed) | 5 |
| 2026-08-24 | M7 phase 2.5 | make lint + make test (628 passed) | 8 |
| 2026-08-25 | M7 context profile | make lint + make test (617) | 5 |
| 2026-08-24 | M7 phase 3.1 (closure) | make lint | 17 |
| 2026-08-24 | M7 phase 3.1 (closure) | make test (623 passed, 13 awaiting the Dockerfile) | 5 |
| 2026-08-24 | M7 phase 3.2 (clone) | make lint | 6 |
| 2026-08-24 | M7 phase 3.2 (clone) | make test (633 passed, 13 awaiting the Dockerfile) | 6 |
| 2026-08-24 | M7 phase 3.1 | docker build -f docker/analysis/Dockerfile (cold) | 14 |
| 2026-08-24 | M7 phase 3.1/3.4 | make lint + make test (648 passed) | 7 |
| 2026-08-24 | M7 phase 3.4 | docker run, diff_analysis request (stated refusal, exit 1) | 2 |
| 2026-08-24 | M7 phase 3.3 | docker run, iac_analysis on platform-infra, 1 live analysis call | 18 |
| 2026-08-24 | M7 phase 3.3 | docker run, code_analysis on datacatalog, 1 live analysis call | 26 |
| 2026-08-24 | M7 phase 3.3 | gather probe on a real datacatalog clone (no model call) | 5 |
| 2026-08-24 | M7 phase 4.3 | pytest tests/unit/test_kubernetes_job_runner.py (red, then green) | 10 |
| 2026-08-24 | M7 phase 4.3 | make lint + make test (615 passed) | 8 |
| 2026-08-24 | M7 phase 4.1 | kubeconform -strict deploy/*.yaml (10 resources) | 1 |
| 2026-08-24 | M7 phase 4.1 | make lint + make test (622 passed) | 5 |
| 2026-08-24 | M7 phase 5.3 | pytest tests/integration/test_poller.py (red, then green) | 2 |
| 2026-08-24 | M7 phase 5.2 | make lint + make test (628 passed) | 6 |
| 2026-08-24 | M7 phase 5.2 | live poller tick, real Datadog, 25-minute window, 20 events | 2 |
| 2026-08-24 | M7 phase 5.1 | pytest tests/integration/test_registered_graphs.py | 1 |
| 2026-08-24 | M7 phases 4-5 | make lint + make test (636 passed) | 6 |
| 2026-08-25 | M7/4 gvisor | make lint + make test (638) + kubeconform | 5 |
| 2026-08-25 | M7 merge | make lint + make test (698) on the four branches merged | 8 |
| 2026-08-25 | M8 phase 1.1 | make capture-errors (13 live read-only calls, 1 throttle) | 8 |
| 2026-08-25 | M8 phase 1.1 | make lint + make test (698 passed) | 15 |
| 2026-08-25 | M8 phases 1.2/1.4/1.5/1.6 | make lint + make test (725 passed) | 14 |
| 2026-08-25 | M8 phases 1.3/1.7 | make lint + make test (762 passed) | 14 |
| 2026-08-25 | M8 phase 1.7 | make run-errors, live hourly tick (17 issues, none new) | 1 |
| 2026-08-25 | M8 phase 1.7 | make run-errors ARGS="--hours 168", catch-up clamped to 6 h | 1 |
| 2026-08-25 | M8 phase 1 (close) | make lint + make test (762 passed) | 8 |
| 2026-08-25 | M8 phases 2.1/2.2 | make lint + make test (777 passed) | 10 |
| 2026-08-25 | M8 phase 2.3 | pytest tests/unit/test_error_gate.py (red, then green) | 1 |
| 2026-08-25 | M8 phases 2.3-2.6 | pytest tests/integration/test_error_groups.py | 1 |
| 2026-08-25 | M8 phases 2.3-2.6 | make lint + make test (854 passed) | 9 |
| 2026-08-25 | M8 phase 3 (probe) | hand-run Datadog probes: exemplar/join/spans/logs/metrics/retention, 45 live read-only calls | 320 |
| 2026-08-25 | M8 phase 3 | pytest tests/unit/test_error_collection.py | 1 |
| 2026-08-25 | M8 phases 3.1-3.5 | make lint + make test (875 passed) | 10 |
| 2026-08-25 | M8 4.1 | make lint && make test | 9 |
| 2026-08-25 | M8 4.2-4.3 | make lint && make test (899 passed) | 8 |
| 2026-08-25 | M8 4.4 | make lint && make test (910 passed) | 10 |
| 2026-08-25 | M8 4.5-4.6 | make lint && make test (921 passed) | 9 |
| 2026-08-25 | M8 phase 4 | make run-errors (live, read-only, no model call) | 2 |
| 2026-08-25 | M8 phase 4 | make run-errors ARGS="--hours 72 --analyse" (live, real tiers) | 56 |
| 2026-08-25 | M8 phase 4 | make lint && make test (924 passed) | 8 |
| 2026-08-25 | M8 phase 4 close-out | make lint && make test (924 passed) | 8 |
| 2026-08-25 | M8 fixes | live probe: error issues across 1/6/13/24h windows | 9 |
| 2026-08-25 | M8 fixes | make lint && make test (935 tests) | 12 |
| 2026-08-25 | M8 fixes | make run-errors ARGS="--hours 24" (read-only) | 2 |
| 2026-08-25 | M8 fixes | make run-errors ARGS="--hours 24 --analyse" (3 groups) | 145 |
| 2026-08-25 | M8 OTel correction | live probe: error issues + 4 span searches, read-only | 3 |
| 2026-08-25 | M8 OTel correction | make capture-errors ARGS="--hours 24 --slug otel_stacks_20260825 --track trace" | 9 |
| 2026-08-25 | M8 OTel correction 3.6 | pytest tests/unit/test_error_otel.py | 1 |
| 2026-08-25 | M8 OTel correction 3.3/3.6 | pytest tests/unit/test_error_collection.py | 1 |
| 2026-08-25 | M8 OTel correction | make lint && make test (955 passed) | 9 |
| 2026-08-25 | M8 OTel correction | make run-errors ARGS="--hours 24" (read-only) | 2 |
| 2026-08-25 | M8 OTel correction | live collection-only render of one group, no model call | 1 |
| 2026-08-25 | M8 OTel correction | make run-errors ARGS="--hours 13 --analyse" (1 group, real tiers) | 46 |
| 2026-08-25 | M8 OTel correction | make run-errors ARGS="--hours 13 --analyse" (re-run after the window fix) | 55 |
| 2026-08-25 | M8 OTel correction | make lint && make test (957 passed) | 9 |
| 2026-08-25 | M8 gate correction | uv run pytest tests/unit/test_error_gate.py | 1 |
| 2026-08-25 | M8 gate correction | uv run pytest tests/integration/test_error_groups.py | 1 |
| 2026-08-25 | M8 gate correction | make lint | 2 |
| 2026-08-25 | M8 gate correction | make test (967 passed) | 7 |
| 2026-08-25 | M8 gate correction | 24 live hourly ticks replayed, read-only, no model call | 8 |
| 2026-08-25 | M8 gate correction | the same day at six floors (1, 5, 10, 25, 50, 200) | 37 |
