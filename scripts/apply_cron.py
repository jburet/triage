"""Tell the Platform to tick the poller, from the object under ``deploy/`` (ADR-0011).

    make cron                    # what it would do, and what is already scheduled
    make cron ARGS="--apply"     # create them
    make cron ARGS="--apply --replace"   # delete each graph's existing crons first
    make cron ARGS="--file deploy/platform/cron-service-mapping.yaml"   # just the one

A LangGraph Platform cron is not a Kubernetes object — it is a row inside the
Platform — so `kubectl apply` cannot create it and this script can. Given no
``--file`` it reads **every** ``deploy/platform/cron-*.yaml``, because three
passes now run themselves and remembering a flag for two of them is how one ends
up unscheduled. For each it looks at what is already scheduled for that graph and
refuses to add a second schedule: two crons on `alert_poller` is two ticks a
minute, two event searches, and two runs for every alert that passes the gate.

One file's failure does not stop the others — a graph the Platform will not take
is reported and the rest are still applied, and the exit code says something
failed.

Nothing here has ever run against a Platform. What it prints without ``--apply``
is exactly what it would post.
"""

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

import yaml

from triage.config import get_settings
from triage.integrations.platform import PlatformRestClient

CRON_DIR = Path(__file__).resolve().parents[1] / "deploy" / "platform"


def cron_files() -> list[Path]:
    """Every pass that runs itself, in a stable order."""
    return sorted(CRON_DIR.glob("cron-*.yaml"))


BOLD, RESET = "\033[1m", "\033[0m"


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--file", type=Path, default=None, help="one cron object; the default is all of them"
    )
    parser.add_argument("--apply", action="store_true", help="create it, rather than print it")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="delete every cron already scheduled for this graph first",
    )
    return parser.parse_args(argv[1:])


async def main(argv: list[str]) -> int:
    options = parse(argv)
    files = [options.file] if options.file else cron_files()
    if not files:
        print(f"no cron objects under {CRON_DIR}")
        return 2

    settings = get_settings()
    if not settings.platform_url:
        print(
            "TRIAGE_PLATFORM_URL is unset, so there is no Platform to schedule anything on.\n"
            "That is the supported fallback (ADR-0011): each pass runs in-process, and its "
            "schedule is whatever runs `make run-poller`, `make run-errors` or `make run-mapping`."
        )
        return 2

    client = PlatformRestClient(settings.platform_url, settings.platform_api_key)
    print(f"{BOLD}{settings.platform_url}{RESET}")
    worst = 0
    for path in files:
        worst = max(worst, await apply_one(client, path, options))
    return worst


async def apply_one(client: PlatformRestClient, path: Path, options: argparse.Namespace) -> int:
    """One cron object. Its failure is reported and costs the others nothing."""
    spec: dict[str, Any] = yaml.safe_load(path.read_text(encoding="utf-8"))
    assistant, schedule = spec["assistant_id"], spec["schedule"]
    print(f"\n{BOLD}{path.name}{RESET} — {assistant} at {schedule!r}")

    try:
        existing = await client.crons(assistant)
    except Exception as failure:
        print(f"  could not read what is scheduled: {failure}")
        return 1

    for cron in existing:
        print(f"  scheduled already: {cron.cron_id} — {cron.assistant_id} at {cron.schedule!r}")
    if not existing:
        print("  nothing scheduled for this graph")

    if not options.apply:
        print(f"  would POST /runs/crons {spec}")
        return 0

    if existing and not options.replace:
        print(
            f"  {assistant} is already scheduled. Two crons on one graph is two ticks a "
            f"minute; pass --replace to delete the ones above first."
        )
        return 1

    try:
        for cron in existing:
            await client.delete_cron(cron.cron_id)
            print(f"  deleted {cron.cron_id}")
        created = await client.create_cron(
            assistant_id=assistant,
            schedule=schedule,
            payload=spec.get("input") or {},
            metadata=spec.get("metadata") or {},
        )
    except Exception as failure:
        print(f"  not scheduled: {failure}")
        return 1
    print(f"  created {created.cron_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
