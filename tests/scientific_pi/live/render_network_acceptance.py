"""Render isolated, same-node network acceptance resources; never call a cluster."""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--batch", required=True)
    parser.add_argument("--namespace", default="scientific-agent")
    args = parser.parse_args()
    for value in (args.node, args.batch, args.namespace):
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{0,50}", value):
            parser.error("node, batch and namespace must be short DNS labels")
    spec = importlib.util.spec_from_file_location("scientific_pi_renderer", ROOT / "k8s/scientific-pi/render.py")
    renderer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(renderer)
    resources, _ = renderer.render(args.namespace, args.image, "http://llm-service.llm.svc.cluster.local/v1", "llm-trusted")
    policy = copy.deepcopy(next(item for item in resources["items"] if item["kind"] == "NetworkPolicy"))
    worker = next(item for item in resources["items"] if item["kind"] == "Deployment")
    model_access = {key: value for key, value in worker["spec"]["template"]["metadata"]["labels"].items() if key == "llm-client"}
    if model_access.get("llm-client") != "true":
        parser.error("worker template lacks the live model service's llm-client=true admission label")
    name = "spnet-" + args.batch
    common = {"validation-suite": "scientific-pi-network", "validation-batch": args.batch, **model_access}
    restricted = {**common, "network-profile": "restricted"}
    def metadata(resource_name, labels=common):
        return {"name": resource_name, "namespace": args.namespace, "labels": labels}
    policy["metadata"] = metadata(name + "-restricted")
    policy["spec"]["podSelector"] = {"matchLabels": restricted}
    policy["spec"]["ingress"] = []
    runner = '''import json, pathlib, subprocess, sys
profile = sys.argv[1]
observations = {}
for filename in ("ipv6_route", "if_inet6"):
    path = pathlib.Path("/proc/net") / filename
    observations[filename] = path.read_text() if path.exists() else None
print(json.dumps({"scope": "pod-ipv6-observation-only", "profile": profile, **observations}), flush=True)
private = subprocess.run([sys.executable, "/validation/network_probe.py", "--profile", profile], timeout=100)
public = subprocess.run([sys.executable, "/validation/academic_transport_probe.py", "--profile", "worker-" + profile, "--include-bridge"], timeout=300)
sys.exit(public.returncode or private.returncode)
'''
    configmap = {"apiVersion": "v1", "kind": "ConfigMap", "metadata": metadata(name), "data": {
        filename: Path(__file__).with_name(filename).read_text(encoding="utf-8")
        for filename in ("network_probe.py", "academic_transport_probe.py")}}
    configmap["data"]["run_acceptance.py"] = runner
    items = [configmap, policy]
    for profile in ("baseline", "restricted"):
        items.append({"apiVersion": "v1", "kind": "Pod", "metadata": metadata(name + "-" + profile, {**common, "network-profile": profile}),
            "spec": {"nodeName": args.node, "restartPolicy": "Never", "activeDeadlineSeconds": 420,
                "automountServiceAccountToken": False, "terminationGracePeriodSeconds": 5,
                "securityContext": {"runAsUser": 10001, "runAsGroup": 10001, "runAsNonRoot": True,
                                    "seccompProfile": {"type": "RuntimeDefault"}},
                "containers": [{"name": "probe", "image": args.image, "imagePullPolicy": "IfNotPresent",
                    "workingDir": "/app", "command": ["python", "/validation/run_acceptance.py", profile],
                    "env": [{"name": "PYTHONPATH", "value": "/app"}, {"name": "PYTHONDONTWRITEBYTECODE", "value": "1"},
                            {"name": "PYTHONUNBUFFERED", "value": "1"}, {"name": "ACADEMIC_API_REQUEST_INTERVAL_MS", "value": "1000"},
                            {"name": "ACADEMIC_API_RATE_LIMIT_MAX_WAIT_SECONDS", "value": "3"}],
                    "securityContext": {"allowPrivilegeEscalation": False, "readOnlyRootFilesystem": True, "capabilities": {"drop": ["ALL"]}},
                    "resources": {"requests": {"cpu": "100m", "memory": "128Mi"}, "limits": {"cpu": "1", "memory": "512Mi"}},
                    "volumeMounts": [{"name": "validation", "mountPath": "/validation", "readOnly": True},
                                     {"name": "ca", "mountPath": "/validation-ca", "readOnly": True},
                                     {"name": "tmp", "mountPath": "/tmp"}]}],
                "volumes": [{"name": "validation", "configMap": {"name": name}},
                            {"name": "ca", "configMap": {"name": "kube-root-ca.crt"}},
                            {"name": "tmp", "emptyDir": {"sizeLimit": "16Mi"}}]}})
    print(json.dumps({"apiVersion": "v1", "kind": "List", "items": items}, indent=2))


if __name__ == "__main__":
    main()
