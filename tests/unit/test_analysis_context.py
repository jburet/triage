"""What an analysis shows the model, and what it admits it never saw (plan M2 phase 2).

A repository does not fit in a context window, so every summary is produced from
a selection. These tests pin the selection rules, because a gather that silently
drops the file holding the answer produces a confident summary of the wrong
thing — and nothing downstream can tell.
"""

from pathlib import Path

from triage.analysis.context import (
    APPLICATION,
    INVESTIGATION,
    TERRAFORM,
    ContextBudget,
    gather,
    reads,
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


def test_a_helm_chart_is_infrastructure_the_terraform_selection_reads(tmp_path):
    """M6 3.3: the probe timeouts and memory limits that would have explained the
    incident of 2026-08-23 are in `helm/zeenea-platform/values.yaml`, and a
    selection of `*.tf` answered Unknown three times."""
    write(tmp_path, "helm/zeenea-platform/values.yaml", "readinessProbe:\n  timeoutSeconds: 1\n")
    write(tmp_path, "helm/zeenea-platform/Chart.yaml", "name: zeenea-platform\n")
    write(tmp_path, "helm/zeenea-platform/templates/statefulset.yaml", "kind: StatefulSet\n")
    write(tmp_path, "modules/rds/main.tf", 'resource "aws_db_instance" "primary" {}\n')

    assert set(paths_read(tmp_path, TERRAFORM)) == {
        "helm/zeenea-platform/values.yaml",
        "helm/zeenea-platform/Chart.yaml",
        "helm/zeenea-platform/templates/statefulset.yaml",
        "modules/rds/main.tf",
    }


def test_the_application_selection_still_answers_from_application_files(tmp_path):
    """Infrastructure files are read wherever they live; the reverse would put a
    chart's templates and a module's HCL into every repository summary."""
    assert not reads("helm/zeenea-platform/templates/statefulset.yaml", APPLICATION)
    assert not reads("modules/rds/main.tf", APPLICATION)
    assert not reads("modules/rds/variables.tfvars", APPLICATION)


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


def test_the_paths_the_mapping_named_are_read_before_the_profiles_own(tmp_path):
    """M6 3.2: the mapping knows which chart defines this workload and a glob does
    not, so a budget spent on the repository's other modules is the answer lost."""
    write(tmp_path, "modules/eks/main.tf", "module {}\n")
    write(tmp_path, "helm/zeenea-platform/values.yaml", "timeoutSeconds: 1\n")

    context = gather(
        tmp_path,
        TERRAFORM,
        ContextBudget(max_files=1),
        first=["helm/zeenea-platform/values.yaml"],
    )

    assert [f.path for f in context.files] == ["helm/zeenea-platform/values.yaml"]


def test_a_named_path_is_read_once_and_not_again_by_its_glob(tmp_path):
    write(tmp_path, "helm/zeenea-platform/values.yaml", "timeoutSeconds: 1\n")

    context = gather(tmp_path, TERRAFORM, first=["helm/zeenea-platform/values.yaml"])

    assert [f.path for f in context.files] == ["helm/zeenea-platform/values.yaml"]


def test_a_named_path_the_tree_does_not_have_is_reported_rather_than_ignored(tmp_path):
    """The mapping was made against the default branch; the analysis reads a commit,
    and a chart that moved between the two is a gap the diagnosis has to know about."""
    write(tmp_path, "modules/eks/main.tf", "module {}\n")

    context = gather(tmp_path, TERRAFORM, first=["helm/zeenea-platform/values.yaml"])

    assert any("helm/zeenea-platform/values.yaml" in note for note in context.not_examined)


def test_the_payload_carries_tree_files_and_gaps(tmp_path):
    write(tmp_path, "pyproject.toml")

    payload = gather(tmp_path, APPLICATION, ContextBudget()).as_payload()

    assert payload["tree"] == ["pyproject.toml"]
    assert payload["files"][0]["path"] == "pyproject.toml"
    assert "not_examined" in payload


def a_scala_service(root: Path) -> Path:
    """The shape of the repository the 2026-08-24 incident was about.

    Twenty-six build files, a README, workflows, a chart — and every line that
    runs under `src/main/scala`, where the application profile has no pattern.
    """
    for module in ("core", "api", "indexer"):
        write(root, f"{module}/build.sbt", 'name := "x"\n')
        write(root, f"{module}/src/main/scala/com/zeenea/{module}/Server.scala", "object Server\n")
        write(root, f"{module}/src/main/scala/com/zeenea/{module}/Store.scala", "object Store\n")
    write(root, "build.sbt", 'ThisBuild / scalaVersion := "2.13.14"\n')
    write(root, "README.md", "# datacatalog\n")
    write(root, ".github/workflows/ci.yml", "on: push\n")
    write(
        root, "core/src/main/resources/application.conf", "pekko.http.server.idle-timeout = 60s\n"
    )
    return root


def test_an_investigation_reads_the_source_the_summary_profile_cannot_see(tmp_path):
    """Measured on 2026-08-24: `code_analysis` of a 4261-file Scala repository
    selected 47 files and not one line of Scala, so it answered `low` about code
    it had never opened. The summary profile is a list of entry-point *names*,
    and a JVM repository has none of them."""
    a_scala_service(tmp_path)

    summarised = paths_read(tmp_path, APPLICATION)
    investigated = paths_read(tmp_path, INVESTIGATION)

    assert not [path for path in summarised if path.endswith(".scala")]
    assert len([path for path in investigated if path.endswith(".scala")]) == 6


def test_what_configures_the_running_service_is_read_before_its_source(tmp_path):
    """An incident is usually explained by a timeout, a pool size or a limit, and
    on the JVM those are in configuration rather than in code."""
    a_scala_service(tmp_path)

    read = paths_read(tmp_path, INVESTIGATION)
    conf = read.index("core/src/main/resources/application.conf")

    assert conf < min(index for index, path in enumerate(read) if path.endswith(".scala"))


def test_a_selection_that_opened_no_source_at_all_says_so(tmp_path):
    """The failure that hid for a day: 47 files, none of them code, and a `low`
    that read like the model's judgement rather than an empty selection."""
    write(tmp_path, "build.sbt")
    write(tmp_path, "README.md")

    context = gather(tmp_path, INVESTIGATION)

    assert any("no source file" in note for note in context.not_examined)


def test_a_selection_that_did_open_source_says_nothing_of_the_kind(tmp_path):
    a_scala_service(tmp_path)

    context = gather(tmp_path, INVESTIGATION)

    assert not [note for note in context.not_examined if "no source file" in note]


def test_the_summary_profile_is_unchanged_so_invalidation_does_not_widen(tmp_path):
    """`reads()` is what ADR-0015 asks of every file a merge touched. Teaching the
    *summary* to read all source would re-summarise every repository on every
    commit; the investigation is a different profile for exactly that reason."""
    assert not reads("core/src/main/scala/com/zeenea/core/Server.scala", APPLICATION)
    assert reads("core/src/main/scala/com/zeenea/core/Server.scala", INVESTIGATION)


def test_the_two_kinds_that_answer_a_question_select_differently_from_the_two_that_summarise():
    """ "What differs is which files are worth opening" — the entrypoint said so
    while handing both application kinds the same profile."""
    from triage.analysis.entrypoint import ANALYSERS
    from triage.schemas.analysis import AnalysisKind

    assert ANALYSERS[AnalysisKind.SUMMARIZE_REPO].profile is APPLICATION
    assert ANALYSERS[AnalysisKind.CODE_ANALYSIS].profile is INVESTIGATION
