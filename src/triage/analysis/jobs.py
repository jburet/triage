"""Kubernetes Jobs, seen from the graph (architecture §7, ADR-0009).

Three verbs on one resource in one namespace — create, status, delete — which is
exactly the RBAC the Platform's ServiceAccount is granted. Deliberately not the
read-only client M3 needs for events and pods: different verbs, different role,
and one client for both would widen both.

The manifest names the Secret the Job's credentials
come from. Creating either, along with the NetworkPolicy and the narrow database
role the Job writes its result with, is the infra track's work — this module
submits, it does not own the cluster's policy.

:class:`KubernetesJobApi` is written from the API reference and is **unverified
against a live cluster**; the first real submission is an infra-track milestone.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx

from triage.config import AnalysisJobConfig
from triage.schemas.analysis import AnalysisRequest

JOB_TIMEOUT_SECONDS = 900.0
"""ADR-0009: long enough for a large repository, short enough that a wedged Job frees up."""

JOB_DEADLINE_GRACE_SECONDS = 60.0
"""How much longer the graph waits than the Job's own deadline.

Waiting exactly as long as Kubernetes does is a race the graph loses: it reports
"no result" a moment before the API server reports `DeadlineExceeded`, so every
timed-out analysis reads as a hang of unknown cause instead of as the stated
failure it is.
"""

JOB_WAIT_SECONDS = JOB_TIMEOUT_SECONDS + JOB_DEADLINE_GRACE_SECONDS

REQUEST_ENV = "TRIAGE_ANALYSIS_REQUEST"
JOB_NAME_ENV = "TRIAGE_ANALYSIS_JOB_NAME"

WORKSPACE_ENV = "TRIAGE_ANALYSIS_WORKSPACE"
"""Set, the entrypoint clones into it; unset, it reads the directory it was started in.

The image sets it, because nothing clones for a Job's container. The host runner
leaves it unset, because it has already cloned (M7 3.2).
"""
ANALYSIS_COMPONENT = "analysis"

WORKSPACE = "/workspace"
"""Where the clone goes: the only writable place, and the container's working directory."""

_NOBODY = 65532

_SERVICE_ACCOUNT = Path("/var/run/secrets/kubernetes.io/serviceaccount")
_BATCH_API = "/apis/batch/v1"


class JobApiError(RuntimeError):
    """A Job request failed. Carries what the API server said, which is the useful part."""


@dataclass(frozen=True)
class JobStatus:
    active: int = 0
    succeeded: int = 0
    failed: int = 0
    reason: str | None = None
    message: str | None = None


class JobApi(Protocol):
    async def create(self, manifest: Mapping[str, Any]) -> None: ...

    async def status(self, name: str) -> JobStatus: ...

    async def delete(self, name: str) -> None: ...


def job_name(request: AnalysisRequest) -> str:
    """A DNS-1123 label, unique per request, and the key of the result row."""
    return f"triage-{request.kind.value.replace('_', '-')}-{request.request_id.hex[:12]}"


def job_manifest(request: AnalysisRequest, *, name: str, spec: AnalysisJobConfig) -> dict[str, Any]:
    labels = {
        "app.kubernetes.io/part-of": "triage",
        # What the sandbox's NetworkPolicy selects on: the Job's egress is far
        # narrower than the rest of the namespace's, so it needs a label no other
        # Triage pod carries (deploy/networkpolicy-analysis.yaml).
        "app.kubernetes.io/component": ANALYSIS_COMPONENT,
        "triage.zeenea.com/kind": request.kind.value,
    }
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {"name": name, "namespace": spec.namespace, "labels": labels},
        "spec": {
            # No retry: a second attempt would write the result row twice, and a
            # failed analysis is a fact for the diagnosis, not something to hide.
            "backoffLimit": 0,
            "activeDeadlineSeconds": int(JOB_TIMEOUT_SECONDS),
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    **({"runtimeClassName": spec.runtime_class} if spec.runtime_class else {}),
                    "restartPolicy": "Never",
                    # An account with no permissions, and no token to use them
                    # with: the analysis has no business with the Kubernetes API.
                    "serviceAccountName": spec.service_account,
                    "automountServiceAccountToken": False,
                    # What the namespace's `restricted` Pod Security admission
                    # demands, and what the sandbox would want anyway: the Job
                    # reads a tree it did not write and talks to two endpoints.
                    "securityContext": {
                        "runAsNonRoot": True,
                        "runAsUser": _NOBODY,
                        "runAsGroup": _NOBODY,
                        "fsGroup": _NOBODY,
                        "seccompProfile": {"type": "RuntimeDefault"},
                    },
                    "volumes": [
                        {"name": "workspace", "emptyDir": {}},
                        {"name": "tmp", "emptyDir": {}},
                    ],
                    "containers": [
                        {
                            "name": "analysis",
                            "image": spec.image,
                            "workingDir": WORKSPACE,
                            "env": [
                                {"name": REQUEST_ENV, "value": request.model_dump_json()},
                                {"name": JOB_NAME_ENV, "value": name},
                                # git writes its config somewhere; a read-only
                                # root means that somewhere has to be said.
                                {"name": "HOME", "value": WORKSPACE},
                            ],
                            "envFrom": [{"secretRef": {"name": spec.secret_ref}}],
                            "resources": {
                                "requests": dict(spec.resources.requests),
                                "limits": dict(spec.resources.limits),
                            },
                            "securityContext": {
                                "allowPrivilegeEscalation": False,
                                "readOnlyRootFilesystem": True,
                                "capabilities": {"drop": ["ALL"]},
                            },
                            "volumeMounts": [
                                {"name": "workspace", "mountPath": WORKSPACE},
                                {"name": "tmp", "mountPath": "/tmp"},
                            ],
                        }
                    ],
                },
            },
        },
    }


@dataclass
class FakeJobApi:
    """Records manifests and hands out scripted statuses, last one repeating."""

    statuses: Sequence[JobStatus] = ()
    create_error: JobApiError | None = None
    delete_error: JobApiError | None = None
    created: list[dict[str, Any]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    _cursor: int = 0

    async def create(self, manifest: Mapping[str, Any]) -> None:
        if self.create_error is not None:
            raise self.create_error
        self.created.append(dict(manifest))

    async def status(self, name: str) -> JobStatus:
        if not self.statuses:
            return JobStatus(active=1)
        index = min(self._cursor, len(self.statuses) - 1)
        self._cursor += 1
        return self.statuses[index]

    async def delete(self, name: str) -> None:
        if self.delete_error is not None:
            raise self.delete_error
        self.deleted.append(name)


class KubernetesJobApi:
    """In-cluster client over the API server's HTTPS endpoint."""

    def __init__(
        self,
        namespace: str,
        *,
        base_url: str = "https://kubernetes.default.svc",
        token_path: Path = _SERVICE_ACCOUNT / "token",
        ca_path: Path = _SERVICE_ACCOUNT / "ca.crt",
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._namespace = namespace
        self._root = f"{_BATCH_API}/namespaces/{namespace}/jobs"
        self._client = client or httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            verify=str(ca_path),
            headers={
                "Authorization": f"Bearer {token_path.read_text(encoding='utf-8').strip()}",
                "Accept": "application/json",
            },
        )

    @staticmethod
    def _explain(response: httpx.Response) -> str:
        try:
            payload = response.json()
        except ValueError:
            return response.text[:500] or "<empty response>"
        message = payload.get("message") if isinstance(payload, dict) else None
        return str(message or response.text[:500])

    async def create(self, manifest: Mapping[str, Any]) -> None:
        response = await self._client.post(self._root, json=dict(manifest))
        if response.is_error:
            raise JobApiError(f"{response.status_code}: {self._explain(response)}")

    async def status(self, name: str) -> JobStatus:
        response = await self._client.get(f"{self._root}/{name}")
        if response.is_error:
            raise JobApiError(f"{response.status_code}: {self._explain(response)}")
        status = response.json().get("status") or {}
        conditions = status.get("conditions") or []
        failure: dict[str, Any] = next((c for c in conditions if c.get("type") == "Failed"), {})
        return JobStatus(
            active=int(status.get("active") or 0),
            succeeded=int(status.get("succeeded") or 0),
            failed=int(status.get("failed") or 0),
            reason=failure.get("reason"),
            message=failure.get("message"),
        )

    async def delete(self, name: str) -> None:
        response = await self._client.request(
            "DELETE", f"{self._root}/{name}", params={"propagationPolicy": "Background"}
        )
        if response.is_error and response.status_code != httpx.codes.NOT_FOUND:
            raise JobApiError(f"{response.status_code}: {self._explain(response)}")

    async def aclose(self) -> None:
        await self._client.aclose()
