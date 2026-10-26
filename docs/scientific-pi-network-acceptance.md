# Scientific Pi network acceptance: observed results and remaining gaps

## Worker-scoped academic DNS correction, 2026-10-22

The current worker academic bridge now uses [a scoped HTTPS transport](../pi_worker/research_tools/transport.py). It queries `dns.alidns.com` over authenticated HTTPS at fixed public IPv4 addresses `223.5.5.5` and `223.6.6.6`. It validates the requested DNS question, CNAME ownership, globally routable unicast A records, and TTL before connecting to a returned literal IPv4. Original academic hostnames remain the TLS SNI and HTTP Host. There is no system-DNS fallback, proxy-environment use, redirect following, TLS bypass, or fake-IP exception. DNS responses are capped at 64 KiB and academic responses at 4 MiB. Cached answers expire at their DNS TTL, capped at 300 seconds; TTL zero is not cached.

The supported academic API hosts are Crossref, PubMed, arXiv, OpenAlex, and Semantic Scholar. IPv6 answers and private/reserved/benchmark addresses are rejected. The scope is the existing worker `search()` and `verify_identifier()` bridge calls: a `ContextVar` selects the transport and is reset on success or exception. Existing CrewAI callers retain their default HTTP transport. This does not restrict an entire Python process to those domains; the separately enforced Kubernetes policy remains the process-level network boundary.

Read-only observations from the existing API Pod on `orangepi5-max-server1` confirmed that ordinary DNS still returns fake IPs for the three default sources. The same Pod successfully used the new transport for all three fixed HTTPS checks: Crossref HTTP 200 with `work-list` JSON, PubMed HTTP 200 with `einforesult` JSON, and arXiv HTTP 200 with an Atom feed. All three used public IPv4 and verified TLS. The final implementation probe finished in 2.671 seconds. This is an API-Pod baseline, not evidence for the final worker image or restricted Pod.

The same read-only inspection found CoreDNS forwarding to `/etc/resolv.conf`, Calico present on all five nodes, an IPv4-only node Pod CIDR, and only IPv6 loopback/link-local addresses in the API Pod with no usable IPv6 default route. Consequently, an IPv6 network failure here cannot demonstrate enforcement of an IPv6 NetworkPolicy. The transport's IPv6 rejection is covered by local tests; process-level IPv6 acceptance remains unavailable on this IPv4 cluster.

Local research tests pass, including rejection of mixed public/private answers, mismatched DNS questions, unrelated A records, CNAME cycles, private/fake/IPv6 destinations, redirects, oversized bodies, and TLS bypass. Tests also cover pinned socket addressing/SNI/Host, TTL expiry, response reading after socket closure, actual bridge routing, and context cleanup/thread isolation.

The dedicated [acceptance renderer](../tests/scientific_pi/live/render_network_acceptance.py) creates two uniquely labeled Pods on one chosen node and copies the worker renderer's exact egress rules into a policy selecting only the restricted Pod. Both Pods use the same digest, nonroot UID 10001, no capabilities, read-only root, no service-account token, and only the public Kubernetes CA. The [academic probe](../tests/scientific_pi/live/academic_transport_probe.py) additionally exercises real three-source searches and DOI/PMID verification through the bridge. No global DNS or existing service was changed by this network correction.

## Final worker paired results

The `20261022-net2` baseline and restricted Pods both ran the actual worker image `arm-cluster-master:5000/scientific-pi-worker@sha256:af410cb49100f4864c0d445d35079f1ba133a502b7a1862da9b63f371e508dd9` on `orangepi5-max-server1` and exited zero. The observed image IDs, nonroot/security settings, probe source hashes, effective policy, and private comparisons are recorded in [network-worker-observed.json](../../docs/scientific-research-pi/evidence/20261022-arm64/network-worker-observed.json). Raw sanitized logs are [baseline](../../docs/scientific-research-pi/evidence/20261022-arm64/network-worker-baseline.log) and [restricted](../../docs/scientific-research-pi/evidence/20261022-arm64/network-worker-restricted.log).

| Check | Baseline | Restricted |
| --- | --- | --- |
| Internal LLM `/health/live` | HTTP 200, expected response | HTTP 200, expected response |
| Crossref HTTPS | Public IPv4, verified TLS, HTTP 200, work-list JSON | Same |
| PubMed HTTPS | Public IPv4, verified TLS, HTTP 200, einforesult JSON | Same |
| arXiv HTTPS | Public IPv4, verified TLS, HTTP 200, Atom feed | Same |
| Actual bridge search | One real record from each of the three sources | Same |
| Actual DOI and PMID lookup | Both identifiers verified against provider responses | Same |
| Kubernetes, Vault, SQLite, Redis, node SSH | Five recognized application/protocol responses | Five timeouts without application/protocol response |
| Pi Web | Timeout | Timeout: worker-policy attribution unknown |
| Metadata root | Connection refused | Timeout: baseline unavailable, attribution unknown |

The initial `net1` pair exposed a missing dependency in the worker deployment renderer: the live `llm/llm-service` ingress policy, created on 2026-10-17, requires caller label `llm-client=true`. Both initial Pods lacked that label and timed out on model health. The renderer now adds it only for the trusted internal model endpoint; its resource selectors remain unchanged. The final `net2` pair copied that authorization label and passed model health in both profiles. No shared LLM policy was weakened. This health check establishes transport availability; real model inference is covered by the separate model/research acceptance evidence.

The Pi Web Service and EndpointSlice were verified to use port 4180, matching the probe. Its target-side `pi/default-deny` and `frpc-ingress` policies permit incoming traffic only from the `frpc` namespace's `app=frpc` Pods. Thus the scientific namespace baseline has no admitted path to Pi Web; its timeout cannot isolate the worker's egress enforcement. No target policy was modified to manufacture a passing baseline. Metadata and process-level IPv6 enforcement retain the limitations above.

Both completed network acceptance batches are cleaned up; the [cleanup receipt](../../docs/scientific-research-pi/evidence/20261022-arm64/network-worker-cleanup.json) records zero remaining Pods, ConfigMaps, or NetworkPolicies for their unique batch labels. Other acceptance or production resources are outside that cleanup scope.

## Earlier system-DNS matrix

On 2026-10-22 the parent executed the fixed-target, credential-free network probe in baseline and restricted Pods, then repeated the baseline on the restricted Pod's node. **Five private-endpoint denial checks have application/protocol evidence in the same-node baseline and fail in the restricted Pod.** The permitted LLM health endpoint returns HTTP 200 in both profiles. Two private negatives and all three academic HTTPS checks remain unknown.

This report analyzes the complete sanitized JSON result records. It does not claim execution by the report-authoring agent, real worker-image validation, model inference, academic research acceptance, or general Internet access. The parent owns Pod/policy lifecycle and cleanup; this analysis performed no network calls, policy changes, global DNS changes, or deployment actions.

Evidence:

- [Initial baseline](D:/github/armbianbegin/.research-temp/scientific-pi-network-baseline.json): parent reports node `arm-cluster-master`; elapsed 11.402 seconds.
- [Restricted result](D:/github/armbianbegin/.research-temp/scientific-pi-network-restricted.json): parent reports node `orangepi5-max-server1`; elapsed 24.622 seconds.
- [Same-node baseline](D:/github/armbianbegin/.research-temp/scientific-pi-network-samenode-result.json): parent reports Pod `spnet-baseline-samenode-20261022-1214` on `orangepi5-max-server1`; elapsed 11.694 seconds. Availability, HTTP/TLS/protocol results, and resolved IPs match the initial baseline.
- [Probe implementation](../tests/scientific_pi/live/network_probe.py): version `scientific-pi-fixed-network-probe-v1`, eleven fixed targets, 64 KiB maximum response body read, verified HTTPS, no redirects, no credentials or inherited proxy use. Each report retains `policy_verdict=not_evaluated`; the comparisons below are this report's interpretation.

The parent reports that the Pods used the same existing pinned API base image with ID prefix `62f908`, actual Python 3.13, nonroot UID 10001, no capabilities, readonly root, no service-account token, and only a public Kubernetes CA mount. The final worker compatibility image was still being rebuilt. The restricted policy reproduced the scientific worker egress rules with a unique test selector, plus deny-all ingress. The parent reports no existing Pod matched that selector. These configuration facts are attributed to the parent; the sanitized JSON itself records endpoint observations rather than proving Pod/image/policy configuration.

The initial comparison used different nodes. The additional unselected baseline on `orangepi5-max-server1` removes that node-placement confound for the five supported paired denial checks. These are distinct Pods and sequential measurements, so the evidence supports the observed policy boundary rather than claiming a packet-level policy audit or every possible destination.

| Fixed target | Baseline and same-node baseline | Restricted | Interpretation |
| --- | --- | --- | --- |
| LLM `/health/live`, `10.102.135.121:80` | HTTP 200, expected format true, 15 bytes | Same | Required public health access observed; no model inference or API-key authentication tested |
| Kubernetes `/version`, `10.96.0.1:443` | Verified TLS, HTTP 200, JSON shape true, 263 bytes | Hard timeout after DNS | Supported denial check |
| Vault `/v1/sys/health`, `10.103.231.34:8200` | HTTP 200, JSON shape true, 391 bytes | Hard timeout after DNS | Supported denial check; health endpoint only, no secrets accessed |
| SQLite `/health`, `10.104.186.221:8000` | HTTP 200, JSON shape true, 39 bytes | Hard timeout after DNS | Supported denial check; no data query |
| Redis PING, `10.103.125.196:6379` | Recognized NOAUTH response, 34 bytes | Hard timeout after DNS | Supported denial check; protocol availability, no authentication attempted |
| Node SSH, `192.168.137.101:22` | Recognized SSH banner, 41 bytes | Hard timeout after address resolution | Supported denial check; no SSH authentication or command execution |
| Pi Web `/`, `10.105.243.140:4180` | Timeout, no HTTP response | Timeout | Unknown: baseline unavailable; not a passed denial |
| Metadata root `/`, `169.254.169.254:80` | Connection refused, no HTTP response | Timeout | Unknown: baseline unavailable; not a passed denial; no credential paths requested |

All private endpoints resolved to the same addresses across the three reports. Restricted private failures contain no HTTP status, verified TLS, or recognized Redis/SSH response. The probe's per-endpoint `elapsed_seconds` on a hard timeout is the last published worker progress time, not the full timeout duration; `hard_timeout=true` and the configured 3-second budget describe enforcement. Total report elapsed times include the waits and remained below the 85-second suite guard.

Academic HTTPS results are identical in all three reports:

| Source | DNS answer | TLS / HTTP / source format |
| --- | --- | --- |
| Crossref `api.crossref.org` | `198.18.5.132` | Not probed; all unverified |
| PubMed `eutils.ncbi.nlm.nih.gov` | `198.18.5.235` | Not probed; all unverified |
| arXiv `export.arxiv.org` | `198.18.5.129` | Not probed; all unverified |

Each address is in the benchmark/fake-IP range `198.18.0.0/15`. The script records `public_dns_non_global_not_probed`, `tls_verified=false`, null HTTP status, and null expected format. DNS lookup succeeded, but the guard intentionally did not dial these non-global answers. Thus these reports establish neither academic HTTPS availability nor an observed academic connection denied by NetworkPolicy. No source payload was acquired or validated. The exclusion must not be removed or TLS verification disabled to make this test green.

The read-only repository review found an existing explanation: the outer repository's [network policy history](../../docs/network-policy-engine.md) documents OpenClash fake-IP DNS and transparent TCP proxying. Its 2026-09 corrections explain why TCP CONNECTED alone can be misleading and why historical claims that policy engines were broken must not be reused. Those historical DSH changes do not authorize changing the scientific worker boundary.

The current scientific [policy renderer](../k8s/scientific-pi/render.py) permits DNS on UDP/TCP 53 only to `kube-system` Pods labeled `k8s-app=kube-dns`; public IPv4 TCP 443 excluding private, node/link-local, loopback, multicast, and `198.18.0.0/15` ranges; and the selected internal LLM Pods on TCP 8000. It has no public UDP 53, DNS-over-TLS 853, arbitrary private proxy, or public IPv6 permission. The policy is broad public HTTPS, not a source-domain allowlist. No actual proxy credentials or live proxy configuration were inspected.

Candidate next steps, proposed only:

1. Preserve the current policy and fake-IP exclusion while preparing a dedicated academic resolution path. A small acceptance-only option is parent-reviewed per-Pod `hostAliases` for just the three academic names, using globally routable IPv4 addresses obtained through an independently controlled real-DNS lookup. Preserve original HTTP Host/TLS SNI and full certificate verification. Track lookup source, TTL, address rotation, and CNAME/address provenance; static aliases are temporary evidence scaffolding, not a durable resolver or completed HTTPS test. Then rerun baseline/restricted academic HTTPS checks with actual format validation.
2. For a durable dedicated DNS solution, design a worker-scoped resolver or application-specific DoH path with a reviewed public IPv4 TCP 443 bootstrap and real DNS answers. Verify that it bypasses the fake-IP answer source, preserves cluster-local DNS for internal services, rejects non-global academic answers, and does not change shared CoreDNS/router behavior. A separate private resolver needs an explicit worker-only DNS egress rule; it is not permitted by the existing selector automatically. Do not relabel a new resolver `k8s-app=kube-dns` without auditing the shared DNS Service selectors, which could unintentionally alter global DNS backends.
3. A dedicated controlled HTTPS egress relay on an approved public IPv4 TCP 443 is another candidate within the existing transport boundary. It must restrict destination names/ports and DNS results to the academic sources and globally routable addresses, preserve end-to-end hostname/certificate verification, and provide sanitized auditable outcomes. Current runtime child-environment filtering does not forward arbitrary proxy variables, so explicit tool integration would require separate design/review. An existing private proxy on an unapproved port cannot be assumed allowed. No existing relay has been verified or selected by this review.
4. Keep Pi Web and metadata as unknown until an approved baseline can produce the required application response. Do not infer a passed denial from existing timeouts/refusal. After the final worker image is available, repeat scoped acceptance against its actual runtime before attributing these API-base observations to that image.

None of these candidates has been implemented or deployed. Global DNS, OpenClash rules, shared policies, and the `198.18.0.0/15` exclusion remain outside this analysis's changes.
