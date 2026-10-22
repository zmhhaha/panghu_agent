"""Fixed, credential-free source checks using the actual worker HTTPS transport.

Run inside each parent-managed baseline/restricted worker Pod. This prints
observations, not a NetworkPolicy verdict. No API keys, private data or proxy
environment are consumed. A nonzero exit means at least one source is unverified.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import time
import xml.etree.ElementTree as ET

from pi_worker.research_tools.transport import AcademicTransport, RESOLVER_HOST, RESOLVER_IPV4


TARGETS = (
    ("crossref", "api.crossref.org", "/works?rows=1&query.bibliographic=transformer"),
    ("pubmed", "eutils.ncbi.nlm.nih.gov", "/entrez/eutils/einfo.fcgi?db=pubmed&retmode=json"),
    ("arxiv", "export.arxiv.org", "/api/query?search_query=all:transformer&start=0&max_results=1"),
)


def expected_format(name, body):
    if name == "arxiv":
        return ET.fromstring(body).tag == "{http://www.w3.org/2005/Atom}feed"
    data = json.loads(body)
    if name == "crossref":
        return data.get("message-type") == "work-list" and isinstance(data.get("message", {}).get("items"), list)
    return isinstance(data.get("einforesult"), dict)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", choices=("api-baseline", "worker-baseline", "worker-restricted"), required=True)
    parser.add_argument("--include-bridge", action="store_true", help="Also run actual three-provider search and DOI/PMID verification")
    args = parser.parse_args()
    started = time.monotonic()
    client, rows = AcademicTransport(), []
    for name, host, path in TARGETS:
        row = {"source": name, "hostname": host, "tls_verified": False, "http_status": None,
               "expected_format": None, "error": None, "dns_ips": []}
        try:
            row["dns_ips"] = list(client.resolve(host, time.monotonic() + 15))
            row["dns_all_public_ipv4"] = all(ipaddress.ip_address(ip).version == 4 and ipaddress.ip_address(ip).is_global for ip in row["dns_ips"])
            response = client.get("https://" + host + path, timeout=25)
            row.update({"http_status": response.status_code, "tls_verified": True,
                        "body_bytes": len(response.content), "expected_format": expected_format(name, response.content)})
            response.raise_for_status()
        except Exception as exc:
            row["error"] = type(exc).__name__
        rows.append(row)
    bridge_results, identifier_results = [], []
    if args.include_bridge:
        from pi_worker.research_tools.bridge import search, verify_identifier
        for source, _, _ in TARGETS:
            try:
                result = search("transformer", providers=[source], limit=1)
                row = result["searches"][0]
                bridge_results.append({"source": source, "status": row["status"],
                    "record_count": len(result["papers"]), "error": row["error"],
                    "full_text_claimed": any(paper["availability"]["full_text"]["available"] for paper in result["papers"])})
                if source in ("crossref", "pubmed"):
                    kind = "doi" if source == "crossref" else "pmid"
                    identifier = next((paper[kind] for paper in result["papers"] if paper[kind]), None)
                    checked = verify_identifier(kind, identifier) if identifier else None
                    identifier_results.append({"kind": kind, "status": checked["verification"]["status"] if checked else "unknown",
                        "error": checked["verification"]["error"] if checked else "search returned no identifier"})
            except Exception as exc:
                bridge_results.append({"source": source, "status": "error", "error": type(exc).__name__})
    report = {"version": "scientific-pi-academic-transport-probe-v1", "profile": args.profile,
              "scope": "fixed-target-public-source-observations", "policy_verdict": "not_evaluated",
              "resolver_host": RESOLVER_HOST, "resolver_bootstrap_ipv4": RESOLVER_IPV4,
              "system_dns_used_for_academic_requests": False, "proxy_environment_used": False,
              "credentials_used": False, "redirects_followed": False,
              "elapsed_seconds": round(time.monotonic() - started, 3), "results": rows,
              "bridge_searches": bridge_results, "identifier_verifications": identifier_results}
    print(json.dumps(report, sort_keys=True))
    passed = all(row["error"] is None and row["http_status"] == 200 and row["expected_format"] is True for row in rows)
    if args.include_bridge:
        passed &= len(bridge_results) == 3 and all(row["status"] == "success" and row["record_count"] > 0 and not row["full_text_claimed"] for row in bridge_results)
        passed &= len(identifier_results) == 2 and all(row["status"] == "verified" for row in identifier_results)
    return 0 if passed else 3


if __name__ == "__main__":
    raise SystemExit(main())
