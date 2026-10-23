"""Read-only, secret-free inventory used before building an isolated executor."""
import json
import subprocess

K = ["kubectl", "--kubeconfig=/etc/kubernetes/super-admin.conf"]
probe = r'''
import importlib.metadata, json, os, pathlib, yaml
home = pathlib.Path(os.environ.get("HERMES_HOME", "/opt/data"))
config = yaml.safe_load((home / "config.yaml").read_text()) or {}
model = config.get("model", {})
if not isinstance(model, dict): model = {"default": str(model)}
providers = {}
for name, raw in (config.get("providers") or {}).items():
    if isinstance(raw, dict):
        providers[name] = {k: raw[k] for k in ("base_url", "api_mode", "default_model", "models", "key_env") if k in raw}
print(json.dumps({"home": str(home), "model": {k:v for k,v in model.items() if k in ("default", "provider", "base_url", "api_mode")}, "providers": providers, "python": os.sys.version.split()[0]}, ensure_ascii=False))
'''
result = subprocess.run(K + ["exec", "-n", "hermes", "deployment/hermes-web", "-c", "hermes", "--", "/opt/hermes/.venv/bin/python", "-c", probe], capture_output=True, text=True, check=True)
print(result.stdout)
