"""What `langgraph.json` promises the Platform (plan M7 phase 5.1).

The Platform reads this file and imports each entry; an entry naming a module
that moved, or an attribute that is not a compiled graph, is a deployment that
fails at start-up rather than a test that fails here. This is the half of 5.1
that can be checked without a Platform — that the six graphs it would serve
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


def test_it_registers_the_six_graphs_the_architecture_describes():
    assert set(REGISTERED) == {
        "ticket_pipeline",
        "cartography",
        "analysis",
        "incident",
        "alert_poller",
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
