> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/formal/COLLECT_FILM_SOURCES.md`；原文件 SHA-256：`3f252a26dd082299ef9abb4dd8707df5098594283c4a52dbbedf8d615b7e1b46`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

`collect_film_sources.py` prepares the pending real five-minute film for source
review. It does not start or change any production activity.

Run `python -B collect_film_sources.py --plan` first. This reads only the actual
local `state.json`; it reports pending until the real published release and
the production run (`draft_id`, as written by `film_driver.py`) exist. Owner
is fixed to `27714443`; project, run and release identities come from that file.

After root has started the actual production run, call the collector once per
useful module-progress change, for example:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/collect_film_sources.py
```

Every invocation creates a new timestamp/UUID directory under
`formal/source-captures/`; it never overwrites a previous capture. An explicit
`--output` must be a new absolute directory beneath `formal/`, with an existing
canonical parent. The collector reuses `remote_formal.py`'s read-only SQLite
connection inside the one Ready workbench Pod, starts a consistent read
transaction, and never instantiates `Store` or calls any mutation endpoint.

For the separate second-attempt driver, use
`python -B collect_film_sources.py --state attempt2/state.json --plan` and then
the same command without `--plan` after the real response or reconciliation.
The explicit `video.external-production-attempt.v1` state adapter reads its
own `run_id`, adopted plan, nested release and silent prepared request. It
requires the durable intent body, checksum, path and key to match, plus an
actual response receipt or matching reconciled run. Prepared/uncertain states
remain pending. It never borrows the prior failed run, edits driver state or
invents budget/publication flags. The unchanged remote validator independently
checks the actual 12x25-second film and published release custody. Every capture
retains the original selected state bytes and hash, including this schema.

For each exact run-selected `module_version_id` that has been synchronized,
the `files/modules/` capture preserves raw persisted package, version and
operation JSON bytes, plus exact UTF-8 scene and optional transition sources.
It validates package hashes, film/scene/target order, predecessor chain,
runtime configuration, immutable published release custody and offered Skill
package bytes. Pending items are recorded without guessing the latest output
or collecting an unrelated project version. The original frozen release
record and selected plan/run records are preserved as well.

When the real render child appears, `files/snapshot/execution-snapshot.json`
is copied from the original persisted snapshot text without reserialization.
The collector verifies it using the original child configuration, confirms
all original scene and transition source hashes against the decoded bundle,
and records `expected-identity.json` from the separate owner-scoped public GET
render metadata and child record. It never generates a substitute snapshot.
That file and its recorded expected SHA are the inputs to the sparse preview
tool. Snapshot capture does not pause the existing render worker.

`files/artifact-inventory.json` hashes every returned file except itself;
`local-verification.json` confirms transport/extraction hashes. The transport
is a bounded tar stream (768MiB total,128MiB per file,4096 files), rejects
nonregular or unsafe/colliding paths, and preserves a partial archive on any
remote failure. A failed capture cannot be treated as a successful source set.

Source custody checks do not establish artistic quality, actual continuous
motion, or efficient rendering. Review original modules as they arrive for
cached ink layers in `mount`, absolute frame reconstruction, allocation costs
in `renderAt`, and visual/geographic continuity. Actual source collection and
that review remain pending until the real run exists.
