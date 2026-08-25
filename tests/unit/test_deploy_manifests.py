"""The manifests under ``deploy/`` against the object Triage actually submits.

The cluster objects are reviewed as YAML and applied by hand; the Job is built in
Python and POSTed. Nothing but this test stops the two from describing different
sandboxes — and the reviewed one is the one people will believe.
"""

from pathlib import Path

import pytest
import yaml

from tests.conftest import an_analysis_request
from triage.analysis.jobs import job_manifest, job_name
from triage.config import AnalysisJobConfig
from triage.schemas.analysis import AnalysisKind

DEPLOY = Path(__file__).resolve().parents[2] / "deploy"
SPEC = AnalysisJobConfig(namespace="triage", image="REGISTRY/triage-analysis:TAG")


def load(name: str) -> dict:
    return yaml.safe_load((DEPLOY / name).read_text(encoding="utf-8"))


def documents(name: str) -> list[dict]:
    return [doc for doc in yaml.safe_load_all((DEPLOY / name).read_text(encoding="utf-8")) if doc]


@pytest.fixture
def submitted() -> dict:
    request = an_analysis_request(AnalysisKind.CODE_ANALYSIS)
    return job_manifest(request, name=job_name(request), spec=SPEC)


def test_the_job_template_is_the_job_that_gets_submitted(submitted):
    template = load("40-job-analysis-template.yaml")
    assert template["spec"]["backoffLimit"] == submitted["spec"]["backoffLimit"]
    assert template["spec"]["activeDeadlineSeconds"] == submitted["spec"]["activeDeadlineSeconds"]

    reviewed = template["spec"]["template"]["spec"]
    actual = submitted["spec"]["template"]["spec"]
    for field in (
        "restartPolicy",
        "automountServiceAccountToken",
        "serviceAccountName",
        "securityContext",
        "volumes",
    ):
        assert reviewed[field] == actual[field], field

    for field in ("workingDir", "resources", "securityContext", "volumeMounts", "envFrom"):
        assert reviewed["containers"][0][field] == actual["containers"][0][field], field


def test_the_job_template_carries_the_labels_the_network_policy_selects(submitted):
    template = load("40-job-analysis-template.yaml")
    selector = load("30-networkpolicy-analysis.yaml")["spec"]["podSelector"]["matchLabels"]

    for labels in (
        template["spec"]["template"]["metadata"]["labels"],
        submitted["spec"]["template"]["metadata"]["labels"],
    ):
        assert selector.items() <= labels.items()


def test_the_egress_probe_is_selected_by_the_same_policy_as_an_analysis():
    """A probe the policy does not select proves nothing about the sandbox."""
    probe = load("41-job-egress-probe.yaml")["spec"]["template"]["spec"]
    selector = load("30-networkpolicy-analysis.yaml")["spec"]["podSelector"]["matchLabels"]
    labels = load("41-job-egress-probe.yaml")["spec"]["template"]["metadata"]["labels"]

    assert selector.items() <= labels.items()
    assert "runtimeClassName" not in probe


def test_the_sandbox_may_not_reach_slack_datadog_or_the_metadata_service():
    policy = load("30-networkpolicy-analysis.yaml")["spec"]
    assert policy["policyTypes"] == ["Ingress", "Egress"]
    assert policy["ingress"] == []

    ports = {port["port"] for rule in policy["egress"] for port in rule["ports"]}
    assert ports == {53, 443, 4000, 5432}

    cidrs = [
        peer["ipBlock"]["cidr"]
        for rule in policy["egress"]
        for peer in rule["to"]
        if "ipBlock" in peer
    ]
    assert cidrs, "GitHub is named as CIDRs; a policy with none grants nothing"
    assert "0.0.0.0/0" not in cidrs
    assert not any(cidr.startswith("169.254.") for cidr in cidrs)


def test_the_platform_may_only_create_get_and_delete_jobs():
    role = next(doc for doc in documents("20-rbac-analysis-jobs.yaml") if doc["kind"] == "Role")
    assert role["rules"] == [
        {"apiGroups": ["batch"], "resources": ["jobs"], "verbs": ["create", "get", "delete"]}
    ]


def test_the_account_the_analysis_runs_as_holds_nothing():
    accounts = {
        doc["metadata"]["name"]: doc
        for doc in documents("20-rbac-analysis-jobs.yaml")
        if doc["kind"] == "ServiceAccount"
    }
    binding = next(
        doc for doc in documents("20-rbac-analysis-jobs.yaml") if doc["kind"] == "RoleBinding"
    )

    assert accounts["triage-analysis"]["automountServiceAccountToken"] is False
    assert [subject["name"] for subject in binding["subjects"]] == ["triage-platform"]


def test_the_committed_secret_holds_no_secrets():
    secret = load("50-secret-analysis.example.yaml")
    filled = [
        key for key, value in secret["stringData"].items() if value and "CHANGEME" not in value
    ]
    assert filled == ["TRIAGE_LITELLM_URL"]


def test_no_manifest_asks_for_a_runtime_class_no_node_provides(submitted):
    """gVisor went with the agent it was chosen for (ADR-0024).

    A RuntimeClass naming a handler the nodes do not install leaves every Job
    Pending, so a manifest that asks for one on a cluster nobody prepared is the
    failure it was meant to prevent.
    """
    for name in ("40-job-analysis-template.yaml", "41-job-egress-probe.yaml"):
        assert "runtimeClassName" not in load(name)["spec"]["template"]["spec"], name
    assert "runtimeClassName" not in submitted["spec"]["template"]["spec"]
    assert not list(Path("deploy").glob("*runtimeclass*"))
