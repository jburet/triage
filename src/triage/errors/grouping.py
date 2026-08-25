"""The group key, and the collapse across tenants it produces (ADR-0026).

Error Tracking groups an exception per service, and every Zeenea customer runs
its own instance of the same code under its own service name. So Datadog's
grouping is grouping by customer: one bug in ``OdbClient.scala`` arrives as one
issue per tenant it happens to, and reporting each of them reports the tenancy
rather than the bug.

The key is the exception type, the source location and **the repository the
service resolves to** — a rule over fields Datadog already returned and the
mono-tenancy rule that turns ``plt-merck-qa`` into ``platform``. No model call
decides whether two issues are the same defect: a non-deterministic key would
mean the fourth occurrence did not reliably land in the thread the first one
opened, which is the whole point of having a group.

The repository is in the key because a ``NullPointerException`` is not a defect,
it is a symptom that happens everywhere. What makes two issues one defect is
that the same code raised it. Two services running different repositories that
both throw from a same-named file are two findings, and merging them would send
one team another team's bug.

The message is *not* in the key, and that is measured rather than assumed. In
the reference hour the six tenants of the biggest group carry six different
queried entities — ``load_contact_by_id``, ``load_inventory_item_by_path``,
``load_user_by_email_read`` — inside one message shape, so keying on the message
splits the group into three and reports the row that was missing instead of the
code that could not handle it missing. ADR-0026 names the message as the finer
key to reach for only once a group is shown to have merged two defects.
"""

from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass
from typing import TypeAlias

from triage.schemas.common import TimeWindow
from triage.schemas.errors import ErrorGroup, ErrorGroupStatus, ErrorIssue, Novelty


@dataclass(frozen=True)
class ServiceRepository:
    """Which repository's code a running service is, and who owns it."""

    repository: str
    repo_url: str | None = None
    team: str | None = None


ResolveService: TypeAlias = Callable[[str], ServiceRepository | None]
"""Service name to repository, or None when nothing claims it.

A plain callable rather than the repository ladder itself, so this module stays
a rule with no database, no config and no graph in it — the caller resolves and
hands the answers in.
"""


def group_key(
    error_type: str,
    file_path: str,
    function_name: str | None,
    repository: str | None,
    service: str,
) -> str:
    """The rule's output, readable so a row in the table says what it is.

    A service nothing claims keys on *itself*: there is no evidence that two
    unresolved services run the same code, and merging them on the strength of a
    shared exception name would be exactly the guess this module refuses.
    """
    where = repository if repository is not None else f"service:{service}"
    return f"{error_type}|{file_path}|{function_name or ''}|{where}"


def group_issues(
    issues: Sequence[ErrorIssue],
    resolve: ResolveService,
    *,
    regressed: Collection[str] = (),
    seen_as: Novelty = Novelty.NEW,
    counted_over: TimeWindow | None = None,
) -> list[ErrorGroup]:
    """One group per defect, worst first, with the count in every service it was seen in.

    ``regressed`` names the issue ids whose regression reopened them in this
    window. A group any of whose issues regressed is a regression: a fix that did
    not hold is a different report from a defect nobody has seen.

    ``seen_as`` is what the tick was looking at: issues that went new by default,
    or ``continuing`` for the pass over the ones that merely went on happening,
    whose groups may move a total and nothing else (ADR-0030).
    """
    groups: dict[str, ErrorGroup] = {}
    for issue in issues:
        error_type, file_path = issue.error_type, issue.file_path
        if error_type is None or file_path is None:
            continue
        where = resolve(issue.service)
        key = group_key(error_type, file_path, issue.function_name, _name(where), issue.service)
        existing = groups.get(key)
        groups[key] = (
            _started(key, error_type, file_path, issue, where, seen_as, counted_over)
            if existing is None
            else _extended(existing, issue)
        )
        if issue.issue_id in regressed:
            groups[key].novelty = Novelty.REGRESSED
    return sorted(groups.values(), key=lambda group: (-group.occurrences, group.key))


def _name(where: ServiceRepository | None) -> str | None:
    return where.repository if where is not None else None


def _started(
    key: str,
    error_type: str,
    file_path: str,
    issue: ErrorIssue,
    where: ServiceRepository | None,
    seen_as: Novelty,
    counted_over: TimeWindow | None,
) -> ErrorGroup:
    return ErrorGroup(
        key=key,
        error_type=error_type,
        file_path=file_path,
        function_name=issue.function_name,
        repository=_name(where),
        repo_url=where.repo_url if where else None,
        team=where.team if where else None,
        track=issue.track,
        novelty=seen_as,
        services={issue.service: issue.occurrences},
        occurrences=issue.occurrences,
        issue_ids=[issue.issue_id],
        sample_message=issue.error_message,
        counted_over=counted_over,
        first_seen=issue.first_seen,
        last_seen=issue.last_seen,
        first_seen_version=issue.first_seen_version,
        last_seen_version=issue.last_seen_version,
        unanalysable_reason=None if where else _no_repository(issue.service),
        status=ErrorGroupStatus.OPEN if where else ErrorGroupStatus.UNMAPPED,
    )


def _extended(group: ErrorGroup, issue: ErrorIssue) -> ErrorGroup:
    group.services[issue.service] = group.services.get(issue.service, 0) + issue.occurrences
    group.occurrences += issue.occurrences
    group.issue_ids.append(issue.issue_id)
    group.first_seen = min(group.first_seen, issue.first_seen)
    group.last_seen = max(group.last_seen, issue.last_seen)
    group.first_seen_version = group.first_seen_version or issue.first_seen_version
    group.last_seen_version = group.last_seen_version or issue.last_seen_version
    return group


def _no_repository(service: str) -> str:
    return (
        f"no repository is declared as running {service}, so there is no tree to read — "
        f"the group is reported as a gap in Triage's own map and never analysed"
    )
