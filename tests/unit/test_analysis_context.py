"""What an analysis shows the model, and what it admits it never saw (plan M2 phase 2).

A repository does not fit in a context window, so every summary is produced from
a selection. These tests pin the selection rules, because a gather that silently
drops the file holding the answer produces a confident summary of the wrong
thing — and nothing downstream can tell.
"""

from pathlib import Path

from triage.analysis.context import (
    APPLICATION,
    TERRAFORM,
    ContextBudget,
    gather,
)


def write(root: Path, path: str, text: str = "x = 1\n") -> Path:
    target = root / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return target


def paths_read(root: Path, profile=APPLICATION, budget=None) -> list[str]:
    context = gather(root, profile, budget or ContextBudget())
    return [f.path for f in context.files]


def test_manifests_are_read_before_anything_else(tmp_path):
    """Priority order is the whole value of the selection when the budget is tight."""
    write(tmp_path, "src/app/worker.py")
    write(tmp_path, "pyproject.toml", "[project]\nname = 'payments-api'\n")

    context = gather(tmp_path, APPLICATION, ContextBudget(max_files=1))

    assert [f.path for f in context.files] == ["pyproject.toml"]


def test_files_left_unread_are_reported_back(tmp_path):
    write(tmp_path, "pyproject.toml")
    write(tmp_path, "src/app/main.py")
    write(tmp_path, "src/app/models.py")

    context = gather(tmp_path, APPLICATION, ContextBudget(max_files=1))

    assert any("were not read" in note for note in context.not_examined)


def test_a_long_file_is_truncated_and_says_so(tmp_path):
    write(tmp_path, "pyproject.toml", "#" * 5_000)

    context = gather(tmp_path, APPLICATION, ContextBudget(max_file_bytes=100))

    assert context.files[0].truncated
    assert len(context.files[0].text) <= 100
    assert any("truncated" in note for note in context.not_examined)


def test_terraform_state_is_never_read(tmp_path):
    """Roadmap F0: the Terraform analysis reads code, never state."""
    write(tmp_path, "main.tf", 'resource "aws_db_instance" "primary" {}\n')
    write(tmp_path, "terraform.tfstate", '{"serial": 41, "resources": []}\n')
    write(tmp_path, "terraform.tfstate.backup", "{}\n")

    context = gather(tmp_path, TERRAFORM, ContextBudget())

    assert [f.path for f in context.files] == ["main.tf"]
    assert not [entry for entry in context.tree if "tfstate" in entry]


def test_dependency_directories_are_not_walked(tmp_path):
    write(tmp_path, "package.json", '{"name": "web"}\n')
    write(tmp_path, "node_modules/left-pad/package.json", '{"name": "left-pad"}\n')

    context = gather(tmp_path, APPLICATION, ContextBudget())

    assert [f.path for f in context.files] == ["package.json"]
    assert context.tree == ("package.json",)


def test_lock_files_are_skipped(tmp_path):
    write(tmp_path, "package.json", '{"name": "web"}\n')
    write(tmp_path, "package-lock.json", "{}\n")
    write(tmp_path, "uv.lock", "\n")

    assert paths_read(tmp_path) == ["package.json"]


def test_a_binary_file_is_not_sent_as_text(tmp_path):
    (tmp_path / "main.py").write_bytes(b"\x00\x01\x02binary")
    write(tmp_path, "pyproject.toml")

    context = gather(tmp_path, APPLICATION, ContextBudget())

    assert [f.path for f in context.files] == ["pyproject.toml"]
    assert any("not text" in note for note in context.not_examined)


def test_the_tree_is_capped_shallowest_first(tmp_path):
    """A cap that cut alphabetically would show all of one package and none of another."""
    write(tmp_path, "README.md")
    write(tmp_path, "a/b/c/deep.py")
    write(tmp_path, "zzz.py")

    context = gather(tmp_path, APPLICATION, ContextBudget(max_tree_entries=2))

    assert context.tree == ("README.md", "zzz.py")
    assert any("not listed" in note for note in context.not_examined)


def test_the_payload_carries_tree_files_and_gaps(tmp_path):
    write(tmp_path, "pyproject.toml")

    payload = gather(tmp_path, APPLICATION, ContextBudget()).as_payload()

    assert payload["tree"] == ["pyproject.toml"]
    assert payload["files"][0]["path"] == "pyproject.toml"
    assert "not_examined" in payload
