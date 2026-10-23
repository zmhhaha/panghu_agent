# Parse browser first-attempt observation (2026-10-22)

The first real parse browser attempt did **not pass**. Retained here rather than replacing it with the later retry result.

- Live assets loaded: `index-Cb8shtpo.js` and `index-C0RgSHL_.css`.
- Existing project `3e70a772-268b-4151-b6b9-ac2a740904ad` displayed actual ready summary, and retained it after reload.
- New project `204f3776-0e69-4b5b-8066-715b98208372` / `PARSE-DOM-1792642569429619216` created with 201.
- New MD asset `88975f01-c17a-4baa-95a3-e6cc2c79dcea` uploaded with 201 / parse_status queued. Two immediate GET snapshots also reported queued, and DOM showed waiting for parse.
- The next poll through the Playwright route proxy encountered `Route.fetch` ECONNRESET. The route handler did not abort/fulfill the failed browser request, leaving that request unresolved in this test transport. The 35-second DOM expectation timed out with waiting status. No page JavaScript errors were observed. This attempt does not establish whether the reset originated in the proxy or upstream transport, and does not establish a parser hang.
- Follow-up actual Windows-origin HTTP probe returned GET /me 200 in 0.078s, the new fixture GET 200 in 0.141s with **ready / text_summary present / parse_error null**, and existing fixture GET 200 in 0.109s.
- Read-only container inspection: API and coordinator both running, restart_count=0, OOMKilled=false, State.Error empty. API started 2026-10-22T04:02:27Z, coordinator started 04:07:42Z.
- No restart, state mutation or service change was performed. The temporary browser container used --rm and exited.

Test-only follow-up: catch proxy transport errors, record their type without headers, and abort the browser request so the actual client receives a network failure and can use its existing retry lifecycle. A later retry will separately document its result. No production UI change has been justified by this observation.
