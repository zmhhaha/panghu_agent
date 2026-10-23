"""Package the public canonical proxy config and isolated runner; stdlib only."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tarfile


HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
CANONICAL = REPOSITORY / "oauth/k8s/ppt-proxy-configmap.yaml"


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def proxy_block(path):
    """Strict extraction for this canonical file's simple literal-block shape."""
    matches = []
    for document in path.read_text(encoding="utf-8").split("\n---\n"):
        lines = document.splitlines()
        if "kind: ConfigMap" not in lines or "  name: ppt-oidc" not in lines:
            continue
        if "data:" not in lines or lines.count("  oauth2-proxy.cfg: |") != 1:
            raise ValueError("Expected exactly one canonical literal proxy configuration")
        start = lines.index("  oauth2-proxy.cfg: |") + 1
        block = []
        for line in lines[start:]:
            if line.strip() and not line.startswith("    "):
                break
            block.append(line[4:] if line else "")
        content = "\n".join(block).rstrip() + "\n"
        if any(line.lstrip().startswith(("client_secret", "cookie_secret")) for line in content.splitlines()):
            raise ValueError("Canonical public configuration must not include credentials")
        matches.append(content)
    if len(matches) != 1:
        raise ValueError("Expected exactly one ConfigMap/ppt-oidc")
    return matches[0]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, type=Path, help="New dedicated package directory")
    args = parser.parse_args()
    config = proxy_block(CANONICAL)
    args.output.mkdir(parents=True, exist_ok=False)
    inputs = args.output / "input"
    inputs.mkdir()
    for filename in ("synthetic_oidc.py", "run_proxy_validation.sh"):
        shutil.copyfile(HERE / filename, inputs / filename)
    (inputs / "oauth2-proxy.cfg").write_text(config, encoding="utf-8", newline="\n")
    receipt = {"synthetic": True, "canonical": "oauth/k8s/ppt-proxy-configmap.yaml",
        "canonical_sha256": digest(CANONICAL), "proxy_config_sha256": digest(inputs / "oauth2-proxy.cfg"),
        "runtime_source_sha256": {"ppt_service/server/auth.py": digest(HERE.parents[1] / "server/auth.py")},
        "protocol_executed": False,
        "fixture_replacements": ["http_address", "https_address", "oidc_issuer_url", "redirect_url",
                                 "client_id", "upstreams", "authenticated_emails_file", "tls_cert_file", "tls_key_file"],
        "input_sha256": {p.name: digest(p) for p in sorted(inputs.iterdir())}}
    (inputs / "source.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    packet = args.output / "input.tar"
    with tarfile.open(packet, "w") as archive:
        for source in sorted(inputs.iterdir()):
            archive.add(source, arcname="input/" + source.name)
    print(json.dumps({"packet": str(packet.resolve()), "sha256": digest(packet),
                      "protocol_executed": False}, sort_keys=True))


if __name__ == "__main__":
    main()
