"""Turning Datadog responses into something a prompt can afford (ADR-0016).

Reduction happens here, before the model, and never in the prompt. The numbers
come from the reference incident: sixty log entries were 176 KB on the wire —
about 45k tokens for one collector — and forty-five of those sixty lines were the
same message. Sent whole they would have spent most of a run's budget saying one
thing.

Two reductions carry a judgement rather than a size limit.

**A Kubernetes change event is a diff, not a title.** Datadog emits "StatefulSet
… deployed" for any object update, readiness included: the reference incident's
two change events differ only in ``ready_replicas``, 1 → 0. A collector that
trusted the title would have produced a confident, wrong deployment diagnosis, so
what is passed on is the set of fields that actually changed, and "nothing
changed but readiness" is stated as such.

**A log line is a template plus a count.** Deduplicating by normalised message
keeps what varies (which templates, how often, over what span) and drops what
repeats, which is the opposite of what a truncated tail keeps.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

_HEX = re.compile(r"\b[0-9a-f]{8,}\b", re.I)
_UUID = re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b", re.I)
_IP = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}\S*")
_ASSIGNED = re.compile(r"(\w+)=\S+")
_PARENS = re.compile(r"\(([^()]|\([^()]*\))*\)")
_NUMBER = re.compile(r"\b\d+(?:\.\d+)?\b")

MESSAGE_LIMIT = 400
TITLE_LIMIT = 200

INTERESTING_TAGS = (
    "container_name",
    "kube_cluster_name",
    "kube_namespace",
    "kube_stateful_set",
    "pod_name",
    "reason",
    "service",
    "short_image",
)

LIFECYCLE_SOURCES = frozenset(
    {"containerd", "kube_stateful_set", "kube_deployment", "kube_replica_set", "docker"}
)


def _collapse_parens(text: str) -> str:
    """Collapse argument lists to ``(…)``, innermost first, until nothing nests.

    One pass is not enough on real logs: the captured incident's
    ``SourceConnectorConfiguration(Mysql,List(TagsSourceProperty(…)))`` lines are
    four different messages that are one template, and only the repeated collapse
    makes them so.
    """
    while True:
        collapsed = _PARENS.sub("(…)", text)
        if collapsed == text:
            return text
        text = collapsed


def template(message: str) -> str:
    """The shape of a log line, with the parts that vary removed."""
    text = message.strip().split("\n")[0]
    text = _ISO.sub("<t>", text)
    text = _UUID.sub("<id>", text)
    text = _IP.sub("<ip>", text)
    text = _ASSIGNED.sub(r"\1=<v>", text)
    text = _collapse_parens(text)
    text = _HEX.sub("<id>", text)
    text = _NUMBER.sub("<n>", text)
    return text.strip()[:200]


def _clip(value: Any, limit: int) -> str:
    text = str(value or "").strip().replace("%%%", "").strip()
    return text if len(text) <= limit else f"{text[:limit]}…"


def _tags(tags: list[str]) -> list[str]:
    return [tag for tag in tags if tag.split(":", 1)[0] in INTERESTING_TAGS]


def changed_fields(previous: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """What actually differs between the two object specs Datadog attached."""
    return {
        key: {"from": previous.get(key), "to": current.get(key)}
        for key in sorted(set(previous) | set(current))
        if previous.get(key) != current.get(key)
    }


def _event(raw: dict[str, Any]) -> dict[str, Any]:
    attributes = raw.get("attributes", {})
    inner = attributes.get("attributes", {}) or {}
    source = (inner.get("evt") or {}).get("type")
    reduced: dict[str, Any] = {
        "at": attributes.get("timestamp"),
        "source": source,
        "status": inner.get("status") or attributes.get("status"),
        "title": _clip(inner.get("title"), TITLE_LIMIT),
        "message": _clip(attributes.get("message"), MESSAGE_LIMIT),
        "tags": _tags([str(tag) for tag in attributes.get("tags", []) or []]),
    }
    if "change_metadata" in inner:
        changed = changed_fields(inner.get("prev_value") or {}, inner.get("new_value") or {})
        reduced["change"] = {
            "action": (inner.get("change_metadata") or {}).get("action"),
            "resource": (inner.get("changed_resource") or {}).get("name"),
            "changed_fields": changed,
            "verdict": (
                "no specification change: the object was updated but every field is "
                "identical except the ones listed"
                if not _is_spec_change(changed)
                else "specification changed"
            ),
        }
    return reduced


_READINESS_ONLY = frozenset(
    {"ready_replicas", "current_replicas", "updated_replicas", "status", "conditions"}
)


def _is_spec_change(changed: dict[str, Any]) -> bool:
    """Whether a change event describes a deployment, or only a status moving."""
    return bool(set(changed) - _READINESS_ONLY)


def _rank(reduced: dict[str, Any]) -> tuple[int, str]:
    """Warnings and errors first, then lifecycle, then the rest — newest last."""
    status = str(reduced.get("status") or "")
    severity = 0 if status in ("error", "warn") else 1 if "change" in reduced else 2
    return severity, str(reduced.get("at") or "")


def reduce_events(payload: dict[str, Any], max_events: int) -> dict[str, Any]:
    """Keep what says something: alerts, changes, and lifecycle events, capped."""
    raw = payload.get("data", []) or []
    reduced = [_event(item) for item in raw]
    kept = [
        item
        for item in reduced
        if item.get("status") in ("error", "warn")
        or "change" in item
        or item.get("source") in LIFECYCLE_SOURCES
    ]
    ordered = sorted(kept, key=_rank)[:max_events]
    return {
        "count": len(raw),
        "kept": len(ordered),
        "dropped_as_noise": len(raw) - len(kept),
        "events": ordered,
    }


def reduce_logs(payload: dict[str, Any], max_templates: int, max_lines: int) -> dict[str, Any]:
    """Templates with counts, then the newest lines verbatim."""
    raw = payload.get("data", []) or []
    entries = [
        {
            "at": item.get("attributes", {}).get("timestamp"),
            "status": item.get("attributes", {}).get("status"),
            "message": _clip(item.get("attributes", {}).get("message"), MESSAGE_LIMIT),
        }
        for item in raw
    ]
    # Templated from the full message, not from the clipped one: two lines that
    # differ only past the clip are the same template, and clipping first would
    # split them on where the ellipsis happened to land.
    counts: Counter[tuple[str, str]] = Counter(
        (
            str(item.get("attributes", {}).get("status")),
            template(str(item.get("attributes", {}).get("message") or "")),
        )
        for item in raw
    )
    templates = [
        {"status": status, "template": shape, "count": count}
        for (status, shape), count in counts.most_common(max_templates)
    ]
    severe = [entry for entry in entries if entry["status"] in ("error", "warn")]
    lines = (severe or entries)[:max_lines]
    return {
        "count": len(entries),
        "distinct_templates": len(counts),
        "templates": templates,
        "lines": lines,
    }


def reduce_log_aggregate(payload: dict[str, Any]) -> dict[str, Any]:
    buckets = (payload.get("data") or {}).get("buckets", []) or []
    return {
        "by": [
            {**bucket.get("by", {}), "count": (bucket.get("computes") or {}).get("c0")}
            for bucket in buckets
        ]
    }


def _points(pointlist: list[list[float]], limit: int) -> list[list[float]]:
    if len(pointlist) <= limit:
        return pointlist
    step = len(pointlist) / limit
    return [pointlist[int(index * step)] for index in range(limit)]


def reduce_timeseries(payload: dict[str, Any], max_series: int, max_points: int) -> dict[str, Any]:
    series = payload.get("series", []) or []
    reduced = []
    for item in series[:max_series]:
        points = [point for point in item.get("pointlist", []) if point and point[1] is not None]
        values = [point[1] for point in points]
        reduced.append(
            {
                "metric": item.get("metric") or item.get("expression"),
                "scope": item.get("scope"),
                "points": len(points),
                "min": min(values) if values else None,
                "max": max(values) if values else None,
                "first": values[0] if values else None,
                "last": values[-1] if values else None,
                "series": _points(points, max_points),
            }
        )
    return {"series": reduced, "series_count": len(series)}


def reduce_spans(payload: dict[str, Any]) -> dict[str, Any]:
    buckets = payload.get("data", []) or []
    return {
        "buckets": [
            {
                **(bucket.get("attributes", {}).get("by", {}) or {}),
                "count": (bucket.get("attributes", {}).get("computes", {}) or {}).get("c0"),
            }
            for bucket in buckets
        ],
        "bucket_count": len(buckets),
    }


def reduce_monitor(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": payload.get("id"),
        "name": payload.get("name"),
        "type": payload.get("type"),
        "query": payload.get("query"),
        "priority": payload.get("priority"),
        "options": {
            key: value
            for key, value in (payload.get("options") or {}).items()
            if key in ("thresholds", "new_group_delay", "renotify_interval", "evaluation_delay")
        },
        "overall_state": payload.get("overall_state"),
    }


def is_empty(reduced: dict[str, Any]) -> bool:
    """Whether a reduced payload carries anything at all."""
    for key in ("events", "lines", "series", "buckets", "by"):
        if reduced.get(key):
            return False
    return not reduced.get("count")
