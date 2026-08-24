"""Which files in an IaC repository define one workload (plan M6 3.1).

The tree is `platform-infra`'s shape: a chart per workload under `helm/`, a
Terraform workspace per workload, and shared modules that belong to nobody in
particular. What the rule must not do is sweep in the neighbours — a selection
that reads `zeenea-platform-api`'s chart answers about a workload this service
is not, and says nothing about having done so.
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
