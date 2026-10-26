# Actual standalone Docker network isolation verification

Date: 2026-10-22 (Asia/Shanghai).

The parent directly completed the real network fixture after interrupted subagents. This is a standalone Docker proof on 192.168.137.101, not Kubernetes acceptance, Casdoor acceptance, or a paid/model runtime test.

## Evidence

Remote isolated case: `/root/ppt-service-network-parent-20261022-1655`.
Scope: `2cf69ef4-0593-4efe-9180-03d965f3e973`.
Result: `network-isolation-result.json`, `passed:true`.
The eight exact firewall-rule and owned-volume cleanup regressions passed before setup. The fixture used the existing production `Firewall.install/remove` and `secure_container` functions; no firewall rules were relaxed and inter-container communication remained disabled.

Actual listeners were positively checked before negative probes. Worker access to the designated proxy at 172.30.240.2:8080 returned HTTP 200 and incremented its counter once. Access to that proxy's listening port 8081, its egress address, and a dedicated host sentinel on the new bridge was denied. Proxy access to a dedicated non-allowlisted egress peer and the host sentinel was denied. Their counters remained unchanged. DNS and IPv6 attempts failed; the worker was UID 10000, with zero capabilities, no-new-privileges, read-only root, and no Docker socket.

## Cleanup

All three exact container IDs and all three immediately recorded, uniquely associated anonymous `/opt/data` volumes were removed. The host sentinel stopped. Both scoped Docker networks, bridges, firewall chain and INPUT jumps were absent after teardown. Remaining scope containers were empty; cleanup_errors was empty.

Normalized iptables SHA-256 before and after: `814448eab74d5e0f4d551a224e211af896c02e68e391ec7e48e34aeecb242c5d`.
Route SHA-256 before and after: `e50cdbd439474ba81bf12146966585625e914dfa0785229644fd5c70033b8ea3`.

## Limits and preserved failures

No model or upstream requests occurred. The approved public upstream's port 443 positive path was deliberately not exercised. No existing/private service port was probed. Paid execution, real worker/model-proxy lifecycle, Kubernetes enforcement, SSO and publication acceptance remain separate requirements.

The earlier case `/root/ppt-service-network-test-20261022` remains a failed pre-listener attempt (`secure_container_contract_failed`), not retroactively a passing test. The pinned image implicitly creates an anonymous `/opt/data` volume. Two earlier never-started containers were removed before their volume identities were captured; those possible orphan volumes cannot be safely attributed and were not pruned or guessed away. The new passing run records each volume before starting its container and uses exact-container `rm --volumes` cleanup.

The live fixture script journals resource IDs before starts and refuses an existing result. Both convenience wrappers now create a new timestamp/random-suffixed case exclusively; the full wrapper stages the frozen control sources, runs the eight focused regressions and preflight, then runs the live probe. Shell syntax was checked without repeating the already-passing live experiment.
