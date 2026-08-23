"""The rules that join the seed, config.yaml and a running service name.

All of them are rules rather than model calls, and all of them fail loudly: a
repository nobody claims is listed as unclaimed rather than given a team, and a
service name that a multi-tenant repository cannot account for is a conflict
rather than a mapping. A wrong mapping sends every later analysis to the wrong
tree, which is more expensive than no mapping at all.
"""

from __future__ import annotations

from collections.abc import Sequence

from triage.config import Config
from triage.schemas.system_map import SeedEntry


def unclaimed(config: Config, seed: Sequence[SeedEntry]) -> list[str]:
    """Seed repositories no team declares in config.yaml.

    Their team is unknown and stays unknown: config.yaml is where ownership is
    stated, and inferring it from the seed's *role* column would be a guess about
    the one thing Triage must not guess — who gets the ticket.
    """
    return [entry.repository for entry in seed if config.repo_named(entry.repository) is None]
