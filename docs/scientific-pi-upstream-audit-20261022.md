# Scientific Pi upstream skill selection audit — 2026-10-22

Decision: keep the existing original `local-procedures-v1` runtime bundle.
All five requested upstream candidates have now been reviewed for selection;
none is copied into, installed in, or automatically loaded by this worker.
This closes the previous missing-source review, not an upstream execution,
scientific-quality or production-acceptance gate.

## Exact source and evidence

The GitHub repository API identifies the repository as
[`K-Dense-AI/scientific-agent-skills`](https://github.com/K-Dense-AI/scientific-agent-skills),
default branch `main`, not archived. The proposed alternative
`K-Dense-AI/scientific-skills` returned HTTP 404 on this review date; there is no
evidence here of a migration to that name.

The review pins commit
[`593377bb048752abb0152deab9fbb1083a6087df`](https://github.com/K-Dense-AI/scientific-agent-skills/commit/593377bb048752abb0152deab9fbb1083a6087df),
whose GitHub commit metadata records tree
`d4d202beb151c1f1c6c15738be0692d65e0447af` and committer date
`2026-10-19T09:33:56Z`. GitHub reports that the commit signature is not verified;
this audit establishes the retrieved revision and content hashes, not a signed
author identity.

The complete recursive file-tree response was not truncated. All 110 blobs under
the five candidate directories, plus root `README.md` and `LICENSE.md`, were
retrieved from `raw.githubusercontent.com` at that exact commit. Each of these
112 files matched both the API-reported byte length and Git blob SHA-1; a SHA-256
was independently calculated. The referenced `parallel-web/SKILL.md` was also
retrieved and matched its Git blob hash to verify its actual dependency contract.
The [machine-readable audit](scientific-pi-upstream-audit-20261022.json) retains
the source identity, per-file hashes, dependency observations and decisions.

Raw, unmodified review copies and source API responses are in the outer
repository's `.research-temp/scientific-skills-audit-20261022/`; this directory is
not a runtime bundle or a deployment input. The five complete SKILL.md files,
their cross-skill/dependency instructions, relevant workflow references, and
static imports of all 30 bundled Python scripts were examined. Hashing all files
does not claim every algorithm, scientific framework or example in the reference
corpus was independently validated. No downloaded script was executed and no
upstream dependency was installed.

## Licenses and notices

Root
[`LICENSE.md`](https://github.com/K-Dense-AI/scientific-agent-skills/blob/593377bb048752abb0152deab9fbb1083a6087df/LICENSE.md)
contains the MIT License, with `Copyright (c) 2025 K-Dense Inc.`. Its SHA-256 is
`09b02a3c9df3053c55531d503357a9c7cde275970e6c3ceaa1ddf5f0e90b40c1`.
Each of the five SKILL.md frontmatters also declares MIT and identifies K-Dense
Inc. as the skill author. The complete tree contains no separate LICENSE or
NOTICE at the repository root other than `LICENSE.md`, at `skills/`, or inside
these five candidate directories. Unrelated test-fixture licenses elsewhere in
the repository do not change this scoped finding.

MIT requires preservation of its copyright and permission notice in copies or
substantial portions. This license condition is distinct from the instructions
in the skill bodies asking the assistant to cite a K-Dense paper. No upstream
text or code is being incorporated into the worker, so there is no derivative
patch or attribution removal in this decision. Future incorporation would need
the MIT notice alongside any retained copyright/author attribution and an
explicit patch record. The repository software license does not independently
license third-party papers, retrieved full text, datasets, journal checklists or
linked materials.

## Candidate decisions

All paths below are relative to the pinned repository. The linked files use
that fixed commit, not a moving branch.

| Candidate | Entry file SHA-256 | Dependencies and material instructions | Current selection |
| --- | --- | --- | --- |
| [paper-lookup](https://github.com/K-Dense-AI/scientific-agent-skills/blob/593377bb048752abb0152deab9fbb1083a6087df/skills/paper-lookup/SKILL.md) | `fac99456ad404ff9a0d3043709b956b5d6383838ae8e8394f274a5693d8bd294` | curl, network access, Python 3.11+ stdlib scripts; 18 academic services. CORE full text requires a key; NCBI/Semantic Scholar/OpenAlex keys support optional account/rate-limit paths. The body allows reading four named keys from a working-directory `.env`, and includes the citation instruction below. | Not selected. Its wider endpoint/credential workflow and human-readable retrieval output are not the worker's bounded academic bridge and fixed JSONL contract. Existing curated sources remain in use. |
| [scientific-critical-thinking](https://github.com/K-Dense-AI/scientific-agent-skills/blob/593377bb048752abb0152deab9fbb1083a6087df/skills/scientific-critical-thinking/SKILL.md) | `563af70571b7dcd6a96bf2e303bedfdcba623b4e9f082d58f04aacbc961fdae4` | Core analytical guidance needs no network or Python package. Optional explicitly requested figures invoke `scientific-schematics` and need `OPENROUTER_API_KEY`/OpenRouter access. Includes the citation instruction. | Not selected. Its conceptual appraisal guidance is relevant, but installing it would add a second procedure and optional diagram/citation behavior outside this release. The original local skill already covers bounded critique and evidence limitations. |
| [scientific-writing](https://github.com/K-Dense-AI/scientific-agent-skills/blob/593377bb048752abb0152deab9fbb1083a6087df/skills/scientific-writing/SKILL.md) | `511a2096bebecd8cf0f8212b9d262d37154ae1ba566eb385d93ac968682edbd1` | Core prose guidance is platform-neutral. Optional CLIs use Python 3.11+ stdlib and no API keys/network imports. Manuscript workflow requires its own source/claim/consistency/authorship manifests and human evidence-verification declarations. Includes the citation instruction. | Not selected. Manuscript author/submission gates and its marker/CSV/manifest formats differ from this worker's research-brief contract; copying them would falsely imply human verification or incompatible output readiness. |
| [citation-management](https://github.com/K-Dense-AI/scientific-agent-skills/blob/593377bb048752abb0152deab9fbb1083a6087df/skills/citation-management/SKILL.md) | `dc79fe1e8438dcf598040615d5323360c6a7ed0b868fc3ffc82512c871a1e3ff` | Python 3.9+ and `requests`; optional Google Scholar scraping needs `scholarly`. Its required metadata-enrichment phase delegates to `parallel-web` in a referenced workflow. Zotero/pyzotero and diagram skills are additional integration paths. Includes the citation instruction. | Not selected. An unconfigured authenticated Parallel workflow is an actual transitive dependency of the documented enrichment path; BibTeX outputs and scraping are also outside the existing numbered-reference/JSONL protocol. |
| [peer-review](https://github.com/K-Dense-AI/scientific-agent-skills/blob/593377bb048752abb0152deab9fbb1083a6087df/skills/peer-review/SKILL.md) | `a042ccdc76d3399431b9a6c4cd4f98968eab964b1c14baeea4d9e73de82c7d19` | Python 3.11+ stdlib local CLIs, with no network/model/image calls in their static imports. Formal review requires an authorized human reviewer, venue policy/intake declarations, private review channels, and different CSV/Markdown artifacts. Includes the citation instruction. | Not selected. This is a formal manuscript-review workflow, not an unattended research synthesis stage. The worker must not invent publisher authorization, an accountable reviewer or editorial judgments to pass those gates. |

These are selection decisions, not claims that the upstream skills are unsafe or
unusable in their intended contexts. No candidate is blocked by an invented
license uncertainty; all five have an observed MIT declaration. No patch or
partial vendoring is performed: every candidate has `patches: []` and
`runtime_enabled: false` in the audit record.

## Citation and external-service findings

All five entries contain a section named **Citing Scientific Agent Skills**.
It requests that, if the skill materially contributed to a manuscript, report,
presentation or code release, the assistant add the paper
`arXiv:2609.00065` / `10.48550/arXiv.2609.00065` to the references or software
section and tell the user, then says to cite its current version and fetch the
live bibliographic record when network access is available. This is a conditional
upstream citation request, not an unconditional instruction to cite it in every
answer and not an additional condition in the MIT license. Its paper metadata
and scientific claims were not verified in this audit. None of these instructions
is loaded by the worker or inserted into generated research bibliographies.

Specific referenced dependency evidence:

- `citation-management/SKILL.md`, **Phase 2.5**, calls metadata enrichment
  **MANDATORY**. `references/core_workflow.md:180` instructs using the
  **parallel-web skill** for each incomplete entry, and lines 195–227 show
  `parallel-cli search/extract`. The pinned `parallel-web/SKILL.md:5` requires
  `parallel-cli 0.9.3`, network access, and a Parallel API key or CLI login; its
  environment declaration names `PARALLEL_API_KEY`. This dependency is not
  reflected in citation-management's short frontmatter compatibility string.
  This review makes no claim about current Parallel prices or account entitlements.
- `scientific-critical-thinking/SKILL.md:6` and its **Visual Aids (Optional)**
  section make OpenRouter a conditional, explicitly requested diagram dependency.
  It is not required for ordinary critical appraisal.
- `scientific-writing/references/source_ledger.md` mentions `parallel-cli extract`
  as the upstream authors' historical review method. That is not a requirement
  of the writing skill's offline tools, and it is not reported as a runtime
  Parallel dependency here.
- Static AST import inspection found only `requests` and optional `scholarly`
  as third-party Python imports in these candidate scripts. `validate_manifest`
  and `validate_review_intake` are neighboring bundled modules, not third-party
  packages. Static inspection does not constitute execution or endpoint tests.

## Runtime compatibility and closure

The original skill, task template, local license and runtime manifest remain
byte-for-byte unchanged. Manifest SHA-256 remains
`fbfc5770ae54f88c7146e94a3845c7ccb480b45137f308c7fc4237dd78803645`, matching
`BUNDLE_MANIFEST_SHA256` in the trusted runtime factory. The manifest's earlier
`upstream_audit` fields are the initial blocked-fetch snapshot; this dated audit
supersedes that observation for candidate selection without changing the runtime
pin or claiming that the now-reviewed candidates have been installed.

For C1/C2, the completed outcome is a pinned, licensed, dependency-aware rejection
of all five upstream candidates for this release, with the existing original
procedure selected. Inapplicable citation and external-service instructions are
excluded by not loading the candidates; no assertion is made that upstream text
was modified or attribution stripped. Any later adoption must explicitly adapt
formats and external dependencies, preserve the MIT notices, review the patch,
update the runtime bundle revision/hash, and rerun the relevant integration gates.
