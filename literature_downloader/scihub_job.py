"""Create and monitor the per-task SciHub Kubernetes Job."""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Callable


class KubernetesApiError(RuntimeError):
    """Raised when the in-cluster Kubernetes API rejects a request."""


class KubernetesJobClient:
    """Small in-cluster Kubernetes API client for Jobs and ConfigMaps.

    The API image already carries urllib and the service account token is
    mounted by Kubernetes, so this avoids adding a large client dependency.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/token",
        ca_path: str = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt",
        opener: Any = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if base_url:
            self.base_url = base_url.rstrip("/")
        else:
            host = os.getenv("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
            port = os.getenv("KUBERNETES_SERVICE_PORT_HTTPS", "443")
            self.base_url = f"https://{host}:{port}"
        self.token = Path(token_path).read_text(encoding="utf-8").strip()
        context = ssl.create_default_context(cafile=ca_path)
        self.opener = opener or urllib.request.build_opener(urllib.request.HTTPSHandler(context=context))
        self.sleep = sleep

    def _request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            method=method,
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {self.token}",
                **({"Content-Type": "application/json"} if data is not None else {}),
            },
        )
        try:
            with self.opener.open(request, timeout=30) as response:
                raw = response.read()
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:1000]
            raise KubernetesApiError(f"Kubernetes API {method} {path} failed ({exc.code}): {detail}") from exc
        except OSError as exc:
            raise KubernetesApiError(f"Kubernetes API {method} {path} failed: {exc}") from exc
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise KubernetesApiError(f"Kubernetes API returned invalid JSON for {method} {path}") from exc

    @staticmethod
    def _path(namespace: str, resource: str, name: str | None = None) -> str:
        suffix = f"/{name}" if name else ""
        return f"/api/v1/namespaces/{namespace}/{resource}{suffix}"

    def create_config_map(self, namespace: str, manifest: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", self._path(namespace, "configmaps"), manifest)

    def delete_config_map(self, namespace: str, name: str) -> None:
        self._request("DELETE", self._path(namespace, "configmaps", name))

    def create_job(self, namespace: str, manifest: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", f"/apis/batch/v1/namespaces/{namespace}/jobs", manifest)

    def get_job(self, namespace: str, name: str) -> dict[str, Any]:
        return self._request("GET", f"/apis/batch/v1/namespaces/{namespace}/jobs/{name}")

    def wait_for_job(self, namespace: str, name: str, timeout_seconds: int, poll_seconds: int) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        while True:
            job = self.get_job(namespace, name)
            status = job.get("status") or {}
            if int(status.get("succeeded") or 0) > 0 or int(status.get("failed") or 0) > 0:
                return job
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(f"SciHub Job timed out: {name}")
            self.sleep(min(max(poll_seconds, 1), remaining))


def build_input_config_map(namespace: str, name: str, identifiers: dict[int, str]) -> dict[str, Any]:
    """Build one ConfigMap containing one identifier file per paper."""
    return {
        "apiVersion": "v1",
        "kind": "ConfigMap",
        "metadata": {"name": name, "namespace": namespace},
        "data": {f"paper-{paper_id}.txt": f"{identifier}\n" for paper_id, identifier in identifiers.items()},
    }


def build_job(
    *,
    namespace: str,
    name: str,
    input_config_map: str,
    image: str,
    pvc_name: str,
    task_id: str,
    round_num: int,
    request_timeout: int,
    job_timeout_seconds: int,
    retries: int,
) -> dict[str, Any]:
    """Build a Job that isolates each paper's SciHub output directory."""
    output_root = f"/app/papers/jobs/{task_id}/round-{round_num}"
    script = "\n".join(
        [
            "set -u",
            "failed=0",
            "found=0",
            "for input_file in /app/input/*.txt; do",
            "  [ -f \"$input_file\" ] || continue",
            "  found=1",
            "  paper_id=\"${input_file##*/}\"",
            "  paper_id=\"${paper_id%.txt}\"",
            f"  output=\"{output_root}/$paper_id\"",
            "  mkdir -p \"$output\"",
            f"  if ! scihub-cli \"$input_file\" --output \"$output\" --parallel 1 --timeout {int(request_timeout)} --retries {int(retries)}; then",
            "    failed=1",
            "  fi",
            "done",
            "[ \"$found\" -eq 1 ] || exit 2",
            "exit \"$failed\"",
        ]
    )
    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": {
            "name": name,
            "namespace": namespace,
            "labels": {"app": "scihub-downloader", "literature-task": task_id, "literature-round": str(round_num)},
        },
        "spec": {
            "backoffLimit": 0,
            "activeDeadlineSeconds": max(int(job_timeout_seconds), 300),
            "ttlSecondsAfterFinished": 86400,
            "template": {
                "metadata": {"labels": {"app": "scihub-downloader", "literature-task": task_id}},
                "spec": {
                    "restartPolicy": "Never",
                    "containers": [
                        {
                            "name": "downloader",
                            "image": image,
                            "imagePullPolicy": "Always",
                            "command": ["/bin/sh", "-c"],
                            "args": [script],
                            "volumeMounts": [
                                {"name": "input", "mountPath": "/app/input", "readOnly": True},
                                {"name": "papers-storage", "mountPath": "/app/papers"},
                            ],
                            "resources": {
                                "requests": {"cpu": "250m", "memory": "256Mi"},
                                "limits": {"cpu": "1", "memory": "1Gi"},
                            },
                        }
                    ],
                    "volumes": [
                        {"name": "input", "configMap": {"name": input_config_map}},
                        {"name": "papers-storage", "persistentVolumeClaim": {"claimName": pvc_name}},
                    ],
                },
            },
        },
    }
