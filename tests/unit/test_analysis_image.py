"""What the analysis image is allowed to carry (plan M7 phase 3.1).

The sandbox is the one place Triage runs code it did not write, against a
repository it cloned a minute ago. ADR-0014 justified dropping the agent loop
partly on "a smaller sandbox": the Job needs GitHub for the clone and the model
proxy, and nothing else. That is a claim about what is *on the path*, so it is
checked the way it is made — by walking the entrypoint's import closure and by
reading the Dockerfile that builds the image, both offline.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src"
DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "analysis" / "Dockerfile"

ENTRYPOINT_MODULE = "triage.analysis.entrypoint"

FORBIDDEN = (
    "triage.graphs",
    "triage.nodes",
    "triage.collect",
    "triage.mapping",
    "triage.scope",
    "triage.runtime",
    "triage.integrations.jira",
    "triage.integrations.slack",
    "triage.integrations.datadog",
    "triage.integrations.platform",
    "triage.integrations.base",
    "triage.integrations.adf",
)


def module_file(name: str) -> Path | None:
    module = SRC / (name.replace(".", "/") + ".py")
    if module.exists():
        return module
    package = SRC / name.replace(".", "/") / "__init__.py"
    return package if package.exists() else None


def _imported(module: str, tree: ast.Module) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names if alias.name.startswith("triage"))
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            if not node.module.startswith("triage"):
                continue
            for alias in node.names:
                candidate = f"{node.module}.{alias.name}"
                found.add(candidate if module_file(candidate) else node.module)
    found.discard(module)
    return found


def closure(root: str = ENTRYPOINT_MODULE) -> set[str]:
    """Every Triage module importing ``root`` executes, packages included.

    Ancestor packages count: importing ``triage.analysis.jobs`` runs
    ``triage/analysis/__init__.py`` and everything it pulls in, which is how a
    module nobody named ends up in the sandbox.
    """
    seen: set[str] = set()
    pending = [root]
    while pending:
        module = pending.pop()
        if module in seen:
            continue
        seen.add(module)
        parts = module.split(".")
        pending.extend(".".join(parts[:depth]) for depth in range(1, len(parts)))
        path = module_file(module)
        if path is not None:
            pending.extend(_imported(module, ast.parse(path.read_text(encoding="utf-8"))))
    return {module for module in seen if module_file(module) is not None}


@pytest.mark.parametrize("banned", FORBIDDEN)
def test_the_entrypoint_reaches_no_graph_and_no_client_it_does_not_need(banned: str) -> None:
    reached = {
        module for module in closure() if module == banned or module.startswith(banned + ".")
    }

    assert not reached, f"the analysis entrypoint imports {', '.join(sorted(reached))}"


def test_the_dockerfile_copies_every_module_the_entrypoint_imports() -> None:
    copied = _copied_paths()
    missing = [
        module for module in sorted(closure()) if not any(_covers(path, module) for path in copied)
    ]

    assert not missing, f"the image would not carry {', '.join(missing)}"


@pytest.mark.parametrize("banned", FORBIDDEN)
def test_the_dockerfile_copies_nothing_the_entrypoint_does_not_import(banned: str) -> None:
    copied = _copied_paths()

    assert not [path for path in copied if _covers(path, banned)], f"the image would carry {banned}"


def _copied_paths() -> list[str]:
    """The ``src/triage/...`` sources of every COPY line, repository-relative."""
    lines = DOCKERFILE.read_text(encoding="utf-8").splitlines()
    return [
        match.group(1).rstrip("/")
        for line in lines
        if (match := re.match(r"COPY\s+(src/triage\S*)\s+\S+\s*$", line.strip()))
    ]


def _covers(path: str, module: str) -> bool:
    target = "src/" + module.replace(".", "/")
    return path in (target, target + ".py") or target.startswith(path + "/")
