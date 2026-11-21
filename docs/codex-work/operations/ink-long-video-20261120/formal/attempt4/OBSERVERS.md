> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/formal/attempt4/OBSERVERS.md`；原文件 SHA-256：`49967bc00ce709d5bd26a8d90b01c7876f5372a40777a50f5fda71038fc6c419`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../../README.md)

These are prepared read-only operators for the fourth standard run. The actual `state.json`, run ID and submit/deployment gates belong to the root operator. No state file was created for these helpers. `--plan` reports pending when state is absent; `--help` is offline. No watcher, capture, download, model call, POST or deployment was executed during preparation.

The request identity is fixed to owner `27714443`, project `7746ffbf-4b69-4586-9980-f79a016d5130`, plan `56fdae8e-bd8b-4683-8502-139aee747581`, key `ink-quiet-film-20261120-run-v4`, silent mode and revision 4. `reuse_completed_prefix_from_run_id` must equal the exact prior run `f2519c6d-4ec4-4d08-ba62-487c3d9acc1c`. The new run ID is read only from the future real submitted/reconciled state.

After the root operator has submitted the run once and recorded its actual response or reconciliation, these commands can run in separate hidden operator processes with fresh stdout/stderr files:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/watch_selected_modules.py --capture-completed --collect-originals
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/follow_new_jobs.py
```

The first command polls selected modules every 15 seconds. For indices 0–3, it verifies the exact pinned prior native receipts locally and records a new-run provenance pointer. It performs no repeated Agent artifact download or model-ledger query for these reused jobs. Their 33 completed model calls remain historical; new model spending is zero. For indices 4–11, it preserves the actual new stopped native artifacts, host Skill receipts and model-count/allocation evidence. New diagnostic followers start only for these suffix jobs. Worker digest `135e8ac2f5915b1cf89fb20a2536af8af55190cae466a5603c2c56ab91942392` remains fixed. The complete Docker R2 remote FD/Pod/container/image/RepoDigest checks and diagnostic allowlist remain byte-identical to the reviewed program.

Each actual status/capture/download checks the new run's `module_reuse`, `module_reuse_sha256` and `module_work_budget` against the real prior run, its stopped child receipts, four original versions/packages, all original module targets and copied prefix intents. The pinned prior run hash is `bac2b99d09cd5d8b349553ca10be62b885303d64b399245196731af066a5a12c`; failure-summary hash is `03e749f726e92ab40966be5aeb7b25c557e032af0753970d0e830c7b93153b43`. New suffix operations keep the original film target namespaces and use the new run's own submission intents.

`collect_film_sources.py` is a strict local copy of the existing collector. Its external-state parser retains the original request schema and additionally accepts the one optional reuse field only when it equals `prior_run_id`. It adds read-only reuse checks and provenance fields; the shared collector and older attempts are unchanged. `collect_originals.py` places all new source captures below this attempt directory. The collector continues to verify the complete scene and transition source inventory and original snapshot bytes.

Individual operations after the actual run exists:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/native_receipts.py status
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/native_receipts.py capture --index 4
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/observe_run.py
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/collect_originals.py
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/review_reused_prefix.py D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/source-captures/ACTUAL_CAPTURE
```

`review_reused_prefix.py` is entirely local. It compares the new-run capture's reused package, operation and version bytes with the original capture and binds them to the already authenticated original native receipts. It creates one immutable review file in the new capture. It does not treat reused receipts as new execution or imply native `read_inputs` evidence.

The selected-module watcher exits at all twelve completed modules or terminal failure. Afterwards use `observe_run.py` to follow the actual render, then run `collect_originals.py` again to preserve its original snapshot. Download only after the actual run and render are completed and stopped, using a new output directory whose parent exists:

```powershell
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/download_actual_film.py --state D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/state.json --plan
python -B D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/download_actual_film.py --state D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/state.json --download --output D:/codex-work/operations/ink-long-video-20261120/formal/attempt4/media-final
```

The downloader verifies actual fourth-run ownership/key, exact reuse provenance, all twelve scene packages plus any transition modules, the original snapshot, stored/media hashes, HTTP range behavior and custody after download. Renderer digest `af8a17ba948d2c17bdaf0a9e176508c2a5bc601285684088892f71af5f649475` remains fixed. Existing 128 MiB media and 24 MiB snapshot limits remain. No real fourth-run path has been exercised during preparation, and final runtime reuse-schema changes must be reviewed before launch. Receipt/source custody does not establish complete decode, silence, continuity or artistic acceptance.
