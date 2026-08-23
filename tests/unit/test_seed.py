"""The seed document, parsed — against the document actually committed here.

The three facts asserted by name are the ones no cluster can answer, and they
are what the whole mapping rests on: `platform` is mono-tenant, `platform-infra`
provisions it, everything else goes through `application-deployer`.
"""

import pytest

from tests.conftest import ARCHITECTURE_DOC, seed_document
from triage.mapping.seed import SeedParseError, parse_document, parse_file
from triage.schemas.system_map import Deployer, Tenancy


@pytest.fixture(scope="module")
def entries():
    return parse_file(ARCHITECTURE_DOC)


def test_every_row_of_the_repository_map_becomes_an_entry(entries):
    """The document's own §1 says twenty repositories; a parse that finds fewer
    has skipped rows and would be a partial import."""
    assert len(entries) == 20
    assert len({entry.repository for entry in entries}) == 20


def test_platform_is_the_only_mono_tenant_repository(entries):
    mono = [entry.repository for entry in entries if entry.tenancy is Tenancy.MONO_TENANT]
    assert mono == ["platform"]


def test_platform_is_provisioned_by_platform_infra(entries):
    platform = next(entry for entry in entries if entry.repository == "platform")
    assert platform.deployment is Deployer.PLATFORM_INFRA
    assert platform.iac_repo == "platform-infra"


def test_every_other_deployed_repository_goes_through_the_application_deployer(entries):
    deployed = [entry for entry in entries if entry.iac_repo is not None]
    assert {entry.iac_repo for entry in deployed} == {"application-deployer", "platform-infra"}
    assert [entry.repository for entry in deployed if entry.iac_repo == "platform-infra"] == [
        "platform"
    ]


def test_a_repository_nothing_deploys_has_no_iac_repository(entries):
    """`zeenea-infra` is a landing zone applied against the AWS organisation; there is
    no deployer repository to point an infrastructure analysis at."""
    infra = next(entry for entry in entries if entry.repository == "zeenea-infra")
    assert infra.deployment is Deployer.AWS_ORGANIZATION
    assert infra.iac_repo is None


def test_markup_is_stripped_from_the_cells(entries):
    gateway = next(entry for entry in entries if entry.repository == "zeenea-api-gateway")
    assert gateway.role == "Internet-facing reverse proxy"


def test_an_unrecognised_tenancy_is_reported_by_repository_rather_than_defaulted():
    document = seed_document(
        "| `new-thing` | Something | Go | Sharded per region | EKS via `application-deployer` |"
    )
    with pytest.raises(SeedParseError, match=r"new-thing: tenancy 'Sharded per region'"):
        parse_document(document)


def test_an_unrecognised_deployment_is_reported_by_repository_rather_than_defaulted():
    document = seed_document("| `new-thing` | Something | Go | Multi-tenant (shared pod) | Nomad |")
    with pytest.raises(SeedParseError, match=r"new-thing: deployment 'Nomad'"):
        parse_document(document)


def test_one_unrecognised_row_stops_the_whole_import():
    """A partial import is a map that is missing repositories without saying so."""
    document = seed_document("| `new-thing` | Something | Go | Sharded | Nomad |")
    with pytest.raises(SeedParseError) as raised:
        parse_document(document)
    assert "not imported at all" in str(raised.value)


def test_a_restructured_table_is_an_error_rather_than_wrong_cells():
    document = seed_document(
        "| `platform` | Mono-tenant (StatefulSet per tenant) |",
        header="| Repository | Tenancy Model |",
        separator="| --- | --- |",
    )
    with pytest.raises(SeedParseError, match="restructured"):
        parse_document(document)


def test_a_document_without_the_repository_map_is_an_error():
    with pytest.raises(SeedParseError, match=r"no '### 1\.1 Repository Map' section"):
        parse_document("# Some other document\n")
