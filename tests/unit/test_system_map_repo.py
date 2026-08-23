"""The three system-map queries, against the in-memory implementation.

The SQL implementation has the same semantics by construction; what is worth
pinning down here is the contract every later feature reads through — that
``(kind, name)`` is the key, that a re-run updates rather than duplicates, and
that an absent service is ``None`` rather than an empty entry.
"""

import pytest

from tests.conftest import a_module_entry, a_service_entry
from tests.conftest import map_row as row
from triage.db.repo import InMemoryRepository
from triage.schemas import SystemMapKind


async def test_entries_are_keyed_by_kind_and_name():
    repo = InMemoryRepository()
    written = await repo.upsert_system_map_entries(
        [
            row(a_service_entry(), SystemMapKind.SERVICE),
            row(a_module_entry(), SystemMapKind.TERRAFORM_MODULE),
        ]
    )

    assert written == 2
    assert {(e.kind, e.name) for e in repo.system_map.values()} == {
        (SystemMapKind.SERVICE, "payments-api"),
        (SystemMapKind.TERRAFORM_MODULE, "modules/payments"),
    }


async def test_a_name_may_repeat_across_kinds():
    repo = InMemoryRepository()
    await repo.upsert_system_map_entries(
        [
            row(a_service_entry(name="payments"), SystemMapKind.SERVICE),
            row(a_module_entry(name="payments"), SystemMapKind.TERRAFORM_MODULE),
        ]
    )
    assert len(repo.system_map) == 2


async def test_re_running_updates_in_place_rather_than_duplicating():
    repo = InMemoryRepository()
    await repo.upsert_system_map_entries([row(a_service_entry(), SystemMapKind.SERVICE)])
    await repo.upsert_system_map_entries(
        [row(a_service_entry(source_commit="ffffff0"), SystemMapKind.SERVICE)]
    )

    assert len(repo.system_map) == 1
    entry = await repo.system_map_for_service("payments-api")
    assert entry is not None
    assert entry.source_commit == "ffffff0"


async def test_service_lookup_returns_what_a_location_needs():
    repo = InMemoryRepository()
    await repo.upsert_system_map_entries([row(a_service_entry(), SystemMapKind.SERVICE)])

    entry = await repo.system_map_for_service("payments-api")
    assert entry is not None
    assert entry.repo_url == "github.com/org/payments-api"
    assert entry.team == "payments"
    assert entry.source_commit == "9f2c1ab"
    assert [point.path for point in entry.summary.entry_points] == [
        "src/payments/main.py",
        "src/payments/worker.py",
    ]


async def test_an_unmapped_service_is_none_not_an_empty_entry():
    repo = InMemoryRepository()
    assert await repo.system_map_for_service("ledger-api") is None


async def test_a_terraform_module_is_not_returned_as_a_service():
    repo = InMemoryRepository()
    await repo.upsert_system_map_entries(
        [row(a_module_entry(name="payments-api"), SystemMapKind.TERRAFORM_MODULE)]
    )
    assert await repo.system_map_for_service("payments-api") is None


async def test_last_summarised_commit_is_read_back_per_repository():
    repo = InMemoryRepository()
    await repo.upsert_system_map_entries(
        [
            row(a_service_entry(), SystemMapKind.SERVICE),
            row(a_module_entry(), SystemMapKind.TERRAFORM_MODULE),
        ]
    )

    assert await repo.last_summarised_commit("github.com/org/payments-api") == "9f2c1ab"
    assert await repo.last_summarised_commit("github.com/org/infra") == "abc1234"


async def test_last_summarised_commit_is_none_for_an_unseen_repository():
    repo = InMemoryRepository()
    assert await repo.last_summarised_commit("github.com/org/ledger-api") is None


async def test_last_summarised_commit_ignores_a_row_that_recorded_no_commit():
    repo = InMemoryRepository()
    await repo.upsert_system_map_entries(
        [row(a_service_entry(source_commit=None), SystemMapKind.SERVICE)]
    )
    assert await repo.last_summarised_commit("github.com/org/payments-api") is None


@pytest.mark.parametrize("kind", list(SystemMapKind))
async def test_every_kind_round_trips_through_its_payload(kind):
    repo = InMemoryRepository()
    entry = a_service_entry() if kind is SystemMapKind.SERVICE else a_module_entry()
    await repo.upsert_system_map_entries([row(entry, kind)])
    (stored,) = repo.system_map.values()
    assert stored.payload["repo_url"] == entry.repo_url


async def test_advancing_the_commit_moves_every_row_of_that_repo_without_touching_the_summary():
    """The carry-forward path: nothing the summariser reads changed, so the map is
    unchanged but is now known to be current as of the merged commit (ADR-0015)."""
    repo = InMemoryRepository()
    await repo.upsert_system_map_entries(
        [
            row(a_service_entry(), SystemMapKind.SERVICE),
            row(a_module_entry(), SystemMapKind.TERRAFORM_MODULE),
        ]
    )
    before = await repo.system_map_for_service("payments-api")

    moved = await repo.advance_source_commit("github.com/org/payments-api", "ffffff1")

    after = await repo.system_map_for_service("payments-api")
    assert moved == 1
    assert after.source_commit == "ffffff1"
    assert after.summary == before.summary
    assert await repo.last_summarised_commit("github.com/org/payments-api") == "ffffff1"
    assert await repo.last_summarised_commit("github.com/org/infra") == "abc1234"


async def test_advancing_the_commit_of_a_repo_the_map_does_not_know_changes_nothing():
    repo = InMemoryRepository()

    assert await repo.advance_source_commit("github.com/org/unknown", "ffffff1") == 0
