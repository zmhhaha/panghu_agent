> 历史归档：原文的版本、命令、准备状态和质量结论只代表其记录时点，不能作为当前部署或新的执行指令。
> 来源：`operations/ink-long-video-20261120/render-canvas2d-recovery/POLICY-OPERATIONS.md`；原文件 SHA-256：`7a38d88754da7db906e5e67c60344b423036b57d7847c47cef09d83f02f1c8f4`。副本进行 UTF-8/LF、Markdown 格式及链接重定位，原件保留。
> [应用设计归档索引](../../../README.md)

These two operators prepare an exact A→B policy and verify original material.
They live outside the repository and build contexts. After initial preparation,
root separately authorized one actual B capture once B61/K8s cleanup completed.
It passed all 36 native samples and pure raw-evidence verification. Root then
authorized actual P/C generation and original-capture dryrun after genuine B
technical and Manim admissions completed. P/C and that dryrun passed. No
production SQLite access, production run or model call has been made here.
Source remains commit
`ac7f7028003fdccb7f8cb90503a74f99c6499824`.

`source_runtime_policy_ops.py` has five modes:

- `inventory` reads the sealed terminal A baseline, capture inventory, original
  run, 13 stopped children, 12 packages and decoded original snapshot. It checks
  package/input/result/module/source hashes and exact predecessor/order/owner/
  target identities. The portable output contains all ordered `MODULE_FIELDS`,
  source/child hashes and original source-file hashes, with no source text.
- `prepare` requires every actual proof reference below before creating P and
  C. P has no target configuration hash; C contains P, then receives its hash.
- `validate` repeats those checks using the real committed contract without
  writing P/C. Run inside the actual B workbench image to validate with its installed
  contract and helpers. Keep absolute artifact paths available at their original
  locations, or mount an immutable copy of the operation tree at those paths.
- `dryrun-capture` uses original packages, all actual B admitted assets and
  measured B hosts to compile a potential B snapshot entirely in memory. It
  does not open a database. `--assets-directory` supplies an exact byte copy of
  actual B `/opt/assets`, checked against all nine admitted input hashes.
  The original project snapshot contains two used ink assets; the original
  package inputs pin all nine admitted assets. Both preserve the original modules,
  silent clock and declared asset bytes. Reports contain hashes, not source.
- `dryrun-live` is for the newly deployed B workbench environment. It loads the
  real environment's admitted release, consumer and P files, opens the provided
  existing database with SQLite `mode=ro`, enables `query_only`, and begins a
  read transaction. It compares the real source run, project, 13 complete child
  documents, outer SQL request identities and 12 original version/package records
  against the authentic capture. It uses the same public
  `FilmCreationWorkflow.authorize_source_runtime_reuse` and
  `module_reuse_material` methods, then the normal pure assembler. Store and
  Creation constructors are deliberately not invoked because they initialize
  schemas. The read view forbids nested business transactions. Gateway, renderer
  and enabled gates are absent; no start/attach/tick/admit method is called.
  `total_changes` must remain zero. It rolls back the read transaction.

All references are `{ "reference": ABSOLUTE_ORDINARY_FILE, "sha256": RAW_SHA }`.
JSON rejects duplicate keys, SHA mismatch, symlinks and missing files. Outputs
are new files/directories and never overwrite existing artifacts.

`policy-inputs.prepared.json` remains intentionally blocked: its unfilled actual
B proof references are null. Copy it to a new immutable, fully populated input
manifest after root supplies the actual evidence. Never fill nulls with fixtures,
the af8 launcher override, a candidate identity or a guessed digest.

Required proof keys and semantics:

| Key | Required original file |
| --- | --- |
| `build` | Actual `build-completed.json`, ordinary renderer/workbench latest digests and the reviewed clean revision. |
| `delta` | Actual `delta-verified.json`: fingerprint differs only in image digest; only browser host changes. |
| `a_measurement`, `b_measurement` | Original actual image measurement JSON. A matches the original release; all assets/licenses/capabilities and 28 publication files match. The browser text hash and exact flag/comment delta are rechecked. |
| `code_and_license_manifest` | The same actual B measurement file; its complete asset/license/publication inventory is verified against A. |
| `execution_admission` | Real B `execution-admission.json`. All three job/media/cancellation evidence files and every referenced original artifact are reopened by raw SHA. |
| `whole_frame` | The actual B admission's media evidence file: ≥61 seconds, continuous 24 fps export, one encoder, zero frame spool, complete independent decode, no OOM and verified removal. This is a technical export, never a claim that the user's 300-second film was delivered. |
| `source_consumer_admission` | Original A consumer admission, whose derived context must equal the full frozen A context. |
| `target_consumer_admission`, `target_consumer_report` | New real B consumer admission and original `admission-report.json`, binding the original producer/package and exact B formal release. |
| `target_consumer_result`, `target_consumer_snapshot`, `target_consumer_capture`, `target_consumer_media` | Original B consumer replay artifacts. Internal admission evidence hashes, decoded snapshot and normal render-result contract are checked. |
| `source_inventory` | Final portable `source-A-policy-source-inventory.json`, recomputed from the original capture before every use. |
| `capture_36point` | `actual-B36-replay.json` from the prepared operator below, including actual B identity and original source/data sampling identity. |

The policy contract intentionally pins a consumer admission's semantic internal
`sha256` in P; the operator inputs additionally pin its raw file SHA. These two
hashes can differ with whitespace. Producer, resource image and helpers remain
exactly unchanged; only the formally admitted consumer release is rebound.

Actual B 36-point replay is prepared in `b36_source_replay.py`. It creates no P.
Only its explicit `capture` command starts a container, after separate root
authorization. It requires actual build/delta paths, exact B `latest@sha256`,
reviewed source revision, ARM image label/ID, sealed A and original capture. It
creates one bounded, labeled, network-disabled, read-only container with only
new work/evidence directories writable. The source capture is mounted read-only.

Inside actual B it validates A's original packages as A, then directly compiles
their unchanged scene/transition source bytes and declared assets with B's real
runtime/host files. It preserves clock, namespace, targets and module hashes,
decodes the new snapshot against B and gives it a fresh, explicit technical ID.
This technical compilation does not perform production package acceptance and
does not bypass the production policy requirement. The original c23b snapshot
and 12 packages are never modified.

The native driver calls installed B `probeFilmBrowser` with its default Chromium
launcher directly. There is no wrapper or added flag. All 36 default opening/
closing/midpoint/before-after frames use fresh reconstruction; the installed
host checks idle double screenshot equality. Original PNGs, requested/completed
frame/rational time, film/manifest/core hashes, native cleanup, actual container
ID/image/exit/OOM and exact owned-container removal are retained. P preparation
reopens all artifacts and checks the decoded B technical snapshot/source/asset
bytes and every frame observation; a status or SHA-shaped string is insufficient.
Sparse stills prove technical reconstruction, not continuous 7,200-frame delivery.

Example preparation commands:

```powershell
python -B source_runtime_policy_ops.py --source ../source inventory --baseline source-A-terminal-baseline.json --capture-files ../formal/attempt6/source-captures/terminal-export-failure-A/files --output NEW_INVENTORY.json
```

The once-dispatched ARM host B36 command used these argument names:

```text
python3 -B b36_source_replay.py capture --source CLEAN_AC7_SOURCE --baseline SEALED_BASELINE --capture-files ORIGINAL_CAPTURE_FILES --build ACTUAL_BUILD_JSON --delta-directory ACTUAL_DELTA_DIRECTORY --image ACTUAL_B_LATEST_DIGEST --revision ac7f7028003fdccb7f8cb90503a74f99c6499824 --output NEW_B36_DIRECTORY
```

After all actual proofs exist:

```text
python3 -B source_runtime_policy_ops.py --source CLEAN_AC7_SOURCE prepare --inputs ACTUAL_INPUTS.json --output NEW_P_C_DIRECTORY
python3 -B source_runtime_policy_ops.py --source CLEAN_AC7_SOURCE dryrun-capture --inputs ACTUAL_INPUTS.json --assets-directory ACTUAL_B_ASSET_COPY --output NEW_OFFLINE_DRYRUN_DIRECTORY
```

Inside deployed B workbench, with the real environment and original absolute
evidence tree available read-only, run `dryrun-live --inputs ACTUAL_INPUTS.json
--database EXISTING_WORKBENCH_DB --output NEW_EXTERNAL_REPORT_DIRECTORY`.
Omit `--source` to use the installed actual B implementation. A virtual target
identifier is used only to hash the potential snapshot/receipt; it is never
inserted. These reports authorize no automatic rollout or production POST.

Executed B36 evidence is in `B36-original-evidence-ac7f7028003f/`; its remote
original is `/root/video-releases/ink-film-20261120/render-canvas2d-recovery/
actual-B36-source-replay-ac7f7028003f`. The executed operator pair is retained
under remote `B36-operators-ac7f7028003f-v2`, with the same bytes preserved in
local `actual-P-C-original-artifacts-ac7f7028003f/executed-B36-operator-v2`.
Keep its original absolute references when validating inside B. The
source-replay completion and transfer receipts record all original hashes.

The first post-capture pure validator failed because its root uid with all
capabilities dropped could not read the new uid10001-owned evidence directory
with mode 0700. Its complete logs/state/removal remain preserved. The only
adaptation was to give group root read/traverse access on that new directory
(uid10001:gid0, mode0750); file bytes and original A stayed untouched. Root's
permitted pure validator then reopened all 36 PNGs and every original reference
at the original absolute paths. It also directly checked that `/opt`'s actually
imported launcher equals `/app`'s measured launcher and the B host hash. Both
pure validators started no browser/encoder/network; every container was removed.
Do not run `capture` again to repair a collection or validation issue.

`B36-vs-prior-sameflag-A-pixel-comparison.json` additionally records all 36 raw
PNG hashes equal to the prior af8 CPU same-flag candidate directory. No GPU
first-frame image was included. This preserves the previous candidate picture
bytes and remains sparse technical evidence, not continuous movie delivery.

Actual P/C are under remote `actual-policy-ac7f7028003f-v2/P-C` and local
`actual-P-C-original-artifacts-ac7f7028003f/actual-policy/P-C`. P semantic SHA is
`dc4f5c1830638f5f4b00ef58e7c9fc1e18aff63889b198729635fe7c4901d3c9`;
C semantic SHA is
`ec4505e6c563eb7aa0e832f6425dcdf2651f72f333f68ae3b8b866962895214d`.
The completion receipt records their separate raw file hashes. The package
dryrun preserved all 12 module hashes and the full 300-second / 24 fps / 7,200
frame silent clock, using nine actual admitted assets. Its virtual snapshot is
`b58f9a4ecbba3efc417b15f3360294fbc24c828a14c5db54d4e48678d1cd8b84`;
it is not a persisted production run or completed movie.

Operator validation uses actual WB B because the renderer image deliberately
lacks Pillow and cannot import the consumer validator. Target renderer B,
original references and runtime evidence are unchanged. Two capture-only
operator fixes select exactly the six trusted hosts (excluding config/index)
and require all nine original input assets instead of the snapshot's two used
assets. Failed pure-attempt logs remain preserved. Existing P/C bytes never
changed. The final helper version is remote `P-C-operators-ac7f7028003f-v4`;
only external operator files changed, and every pure container was removed.

The rollout-after live check should use the prepared
`readonly_live_source_dryrun.py` stdin operator. It embeds sealed P/C and the
authentic source/full13 receipt baseline into the actual Pod, uses real public
facade methods and actual settings assets/hosts, and never needs the host
operation tree mounted in the Pod. It must be dispatched only after root pins
the newly Ready Pod identity. Its prepared inputs remain blocked for missing
actual Pod identity until that postflight.
