"""What `langgraph.json` promises the Platform (plan M7 phase 5.1).

The Platform reads this file and imports each entry; an entry naming a module
that moved, or an attribute that is not a compiled graph, is a deployment that
fails at start-up rather than a test that fails here. This is the half of 5.1
that can be checked without a Platform — that the graphs it would serve
import, compile and expose their nodes. Whether a Platform serves them against
the shared Postgres is a deployment, and there has not been one.
"""

import importlib

import pytest
import yaml
from langgraph.graph.state import CompiledStateGraph

from tests.conftest import REPO_ROOT

REGISTERED: dict[str, str] = yaml.safe_load(
    (REPO_ROOT / "langgraph.json").read_text(encoding="utf-8")
)["graphs"]


def load(entry: str) -> object:
    path, attribute = entry.split(":")
    module = path.removeprefix("./src/").removesuffix(".py").replace("/", ".")
    return getattr(importlib.import_module(module), attribute)


def test_it_registers_the_graphs_the_architecture_describes():
    assert set(REGISTERED) == {
        "ticket_pipeline",
        "cartography",
        "analysis",
        "incident",
        "code_exception",
        "alert_poller",
        "error_poller",
        "service_mapping",
    }


@pytest.mark.parametrize("name", sorted(REGISTERED))
def test_every_registered_graph_imports_and_is_compiled(name):
    graph = load(REGISTERED[name])

    assert isinstance(graph, CompiledStateGraph), f"{name} is not a compiled graph"
    assert graph.nodes, f"{name} compiled with no nodes"


def test_the_poller_the_cron_ticks_has_the_one_node_it_is_named_for():
    graph = load(REGISTERED["alert_poller"])

    assert "poll_alerts" in graph.nodes


def test_the_code_exception_graph_composes_the_two_shared_sub_graphs():
    graph = load(REGISTERED["code_exception"])

    assert {"open_group", "collect_exception", "qualify_exception", "settle_group"} <= set(
        graph.nodes
    )
    assert {"analysis", "ticket_pipeline"} <= set(graph.nodes)


def test_the_error_poller_the_hourly_cron_ticks_reads_then_groups():
    graph = load(REGISTERED["error_poller"])

    assert "poll_error_issues" in graph.nodes
    assert "group_error_issues" in graph.nodes


@pytest.mark.parametrize(
    "module", sorted((REPO_ROOT / "src" / "triage" / "nodes").glob("*.py")), ids=lambda p: p.name
)
def test_no_node_module_stringifies_its_config_annotation(module):
    """LangGraph reads the annotation, so a stringified one loses the injection.

    A module with ``from __future__ import annotations`` annotates its node's
    config parameter as the *string* ``"RunnableConfig | None"``. LangGraph then
    does not recognise the parameter, never passes a config, and every node in
    that graph silently falls back to ``build_deps()`` — its own repository, its
    own Datadog client. Nothing fails; the graph answers empty. Measured on the
    alert poller, whose ``--db`` watermark had been going to an in-memory
    repository while the tick reported success.
    """
    source = module.read_text(encoding="utf-8")

    assert not ("RunnableConfig" in source and "from __future__ import annotations" in source), (
        f"{module.name} annotates config as a string, so its nodes will never be "
        f"given one — drop `from __future__ import annotations`"
    )
