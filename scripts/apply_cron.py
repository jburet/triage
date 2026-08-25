"""Tell the Platform to tick the poller, from the object under ``deploy/`` (ADR-0011).

    make cron                    # what it would do, and what is already scheduled
    make cron ARGS="--apply"     # create it
    make cron ARGS="--apply --replace"   # delete the crons for this graph first

A LangGraph Platform cron is not a Kubernetes object — it is a row inside the
Platform — so `kubectl apply` cannot create it and this script can. It reads
``deploy/platform/cron-alert-poller.yaml``, looks at what is already scheduled for
that graph, and refuses to add a second schedule for the same one: two crons on
`alert_poller` is two ticks a minute, two event searches, and two runs for every
alert that passes the gate.

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

CRON_FILE = Path(__file__).resolve().parents[1] / "deploy" / "platform" / "cron-alert-poller.yaml"

BOLD, RESET = "\033[1m", "\033[0m"


def parse(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=CRON_FILE, help="the cron object to apply")
    parser.add_argument("--apply", action="store_true", help="create it, rather than print it")
    parser.add_argument(
        "--replace",
        action="store_true",
        help="delete every cron already scheduled for this graph first",
    )
    return parser.parse_args(argv[1:])


async def main(argv: list[str]) -> int:
    options = parse(argv)
    spec: dict[str, Any] = yaml.safe_load(options.file.read_text(encoding="utf-8"))
    assistant, schedule = spec["assistant_id"], spec["schedule"]

    settings = get_settings()
    if not settings.platform_url:
        print(
            "TRIAGE_PLATFORM_URL is unset, so there is no Platform to schedule anything on.\n"
            "That is the supported fallback (ADR-0011): the poller runs in-process, and its "
            "schedule is whatever runs `make run-poller`."
        )
        return 2

    client = PlatformRestClient(settings.platform_url, settings.platform_api_key)
    existing = await client.crons(assistant)
    print(f"{BOLD}{settings.platform_url}{RESET}")
    for cron in existing:
        print(f"  scheduled already: {cron.cron_id} — {cron.assistant_id} at {cron.schedule!r}")
    if not existing:
        print("  nothing scheduled for this graph")

    if not options.apply:
        print(f"\nwould POST /runs/crons {spec}")
        print("pass --apply to create it")
        return 0

    if existing and not options.replace:
        print(
            f"\n{assistant} is already scheduled. Two crons on one graph is two ticks a "
            f"minute; pass --replace to delete the ones above first."
        )
        return 1

    for cron in existing:
        await client.delete_cron(cron.cron_id)
        print(f"  deleted {cron.cron_id}")

    created = await client.create_cron(
        assistant_id=assistant,
        schedule=schedule,
        payload=spec.get("input") or {},
        metadata=spec.get("metadata") or {},
    )
    print(f"\ncreated {created.cron_id} — {assistant} at {schedule!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main(sys.argv)))
