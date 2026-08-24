"""Which files in an IaC repository define one workload (plan M6 3.1, ADR-0021).

``TREE`` is a charts repository — one chart per workload under `helm/`, a
Terraform workspace per workload — which is the shape the naming rule was
written for and, as the 2026-08-24 live pass found out, not the shape of
`platform-infra`. What the rule must not do is sweep in the neighbours: a
selection that reads `zeenea-platform-api`'s chart answers about a workload this
service is not, and says nothing about having done so.

``REAL_TREE`` is the real one, where nothing but a declaration can answer.
"""

from triage.mapping.iac import MAX_PATHS, workload_paths

TREE = [
    "README.md",
    "docs/runbook.md",
    "helm/zeenea-platform/Chart.yaml",
    "helm/zeenea-platform/values.yaml",
    "helm/zeenea-platform/templates/statefulset.yaml",
    "helm/zeenea-platform-api/values.yaml",
    "helm/zeenea-connector/values.yaml",
    "terraform/platform/main.tf",
    "terraform/platform/terraform.tfstate",
    "terraform/networking/main.tf",
    "modules/eks/main.tf",
]


def test_the_chart_that_names_the_repository_is_where_the_workload_is_defined():
    paths = workload_paths(TREE, "platform")

    assert "helm/zeenea-platform/values.yaml" in paths
    assert "helm/zeenea-platform/Chart.yaml" in paths
    assert "helm/zeenea-platform/templates/statefulset.yaml" in paths
    assert "terraform/platform/main.tf" in paths


def test_a_neighbouring_workloads_chart_is_not_this_workloads_definition():
    """`platform-api` is its own repository. A prefix match would read its chart
    for every `platform` incident and answer about a workload nobody asked about."""
    paths = workload_paths(TREE, "platform")

    assert "helm/zeenea-platform-api/values.yaml" not in paths
    assert "helm/zeenea-connector/values.yaml" not in paths


def test_what_the_whole_repository_shares_is_left_to_the_profile():
    paths = workload_paths(TREE, "platform")

    assert "modules/eks/main.tf" not in paths
    assert "terraform/networking/main.tf" not in paths


def test_terraform_state_is_never_a_workloads_definition():
    assert "terraform/platform/terraform.tfstate" not in workload_paths(TREE, "platform")


def test_the_values_that_answer_the_question_are_offered_before_the_templates():
    """Priority is the whole value of an ordered list once the budget runs short."""
    paths = workload_paths(TREE, "platform")

    assert paths[0] == "helm/zeenea-platform/values.yaml"
    assert paths.index("helm/zeenea-platform/values.yaml") < paths.index(
        "helm/zeenea-platform/templates/statefulset.yaml"
    )


def test_a_tenant_that_names_its_own_directory_is_found_under_that_name_too():
    tree = [*TREE, "terraform/tenants/plt-hcl-software-uat/main.tf"]

    paths = workload_paths(tree, "platform", "plt-hcl-software-uat")

    assert "terraform/tenants/plt-hcl-software-uat/main.tf" in paths


def test_a_tenant_name_no_directory_carries_adds_nothing():
    assert workload_paths(TREE, "platform", "plt-hcl-software-uat") == workload_paths(
        TREE, "platform"
    )


def test_a_repository_the_tree_says_nothing_about_is_an_empty_answer():
    assert workload_paths(TREE, "ledger-api") == []


def test_the_list_is_capped_and_what_it_drops_the_profile_still_reaches():
    tree = [f"helm/zeenea-platform/templates/{index}.yaml" for index in range(MAX_PATHS * 2)]

    assert len(workload_paths(tree, "platform")) == MAX_PATHS


REAL_TREE = [
    "README.md",
    "terraform/core-eks/main.tf",
    "terraform/database/main.tf",
    "terraform/eks_module/eks.tf",
    "terraform/eks_module/volumes.tf",
    "terraform/eks_module/terraform.tfstate",
    "terraform/storage/main.tf",
]
"""`platform-infra` as it actually is: modules named for what they provision on."""


def test_a_declaration_finds_what_no_path_segment_names():
    """The workload is `resource "kubernetes_stateful_set_v1" "platform"` inside
    `eks.tf`. No segment of that path is `platform`, and no rule over segments
    reaches a name one level below them."""
    paths = workload_paths(
        REAL_TREE, "platform", "plt-hcl-software-uat", declares=["terraform/eks_module/*"]
    )

    assert "terraform/eks_module/eks.tf" in paths
    assert "terraform/eks_module/volumes.tf" in paths


def test_a_declaration_is_the_whole_selection():
    """What the operator declared is where the workload is; the rest of the
    repository provisions the cluster and the database it runs against."""
    paths = workload_paths(REAL_TREE, "platform", declares=["terraform/eks_module/*"])

    assert "terraform/core-eks/main.tf" not in paths
    assert "terraform/database/main.tf" not in paths


def test_a_declaration_that_matches_nothing_answers_nothing():
    """Never the name rule's answer instead. A declaration gone stale — the module
    renamed — is a wrong answer to report, not a reason to fall back to a guess
    the operator overrode precisely because it was wrong."""
    assert workload_paths(TREE, "platform", declares=["terraform/eks_module/*"]) == []


def test_what_is_declared_is_still_ordered_and_filtered_like_anything_else():
    paths = workload_paths(REAL_TREE, "platform", declares=["terraform/eks_module/*"])

    assert "terraform/eks_module/terraform.tfstate" not in paths


def test_the_name_rule_is_what_answers_when_nothing_is_declared():
    assert workload_paths(TREE, "platform") == workload_paths(TREE, "platform", declares=[])
