# Local scientific skill bundle and research tool bridge

This document records the reviewed local skill bundle and its controlled
checks. The [2026-10-22 deployment record](https://github.com/zmhhaha/armbianbegin/blob/codex/scientific-pi-worker/docs/scientific-research-pi/rollout-20261022.md)
is historical evidence for the earlier bundle. The 2026-10-23 writing revision
and its verification limits are described below. Controlled tests verify
contracts, output consistency and failure handling; they do not establish
scientific accuracy or the narrative quality of a real report.

## Bundle and upstream audit

The current pinned bundle revision is `local-procedures-v2`. Its original procedural
skill covers paper lookup, screening/critical thinking, evidence extraction,
writing, citation management and final review in one small skill:
[research-procedure](../scientific_skills/research-procedure/SKILL.md). The
[task template](../scientific_skills/task-template.md) specifies exact artifacts,
claims/citations, explicit access levels and honest unknown counts. The local
[manifest](../scientific_skills/manifest.json) records SHA-256 hashes, dependencies,
license scope and the upstream audit status. The original local procedure/template
text uses the bundled [MIT license](../scientific_skills/LICENSE).

The original fetch of [K-Dense scientific-agent-skills](https://github.com/K-Dense-AI/scientific-agent-skills)
was blocked. That initial observation remains in the runtime manifest's upstream
audit field; each released manifest's full byte hash is pinned by the runtime
factory. On **2026-10-22**, the
[completed selection audit](scientific-pi-upstream-audit-20261022.md) superseded
the missing-source finding: repository identity, exact commit
`593377bb048752abb0152deab9fbb1083a6087df`, MIT license/copyright, all five entry
hashes, dependency paths and citation instructions were retrieved and reviewed.
The [audit inventory](scientific-pi-upstream-audit-20261022.json) records hashes
for 113 source files; all match their pinned Git blob identities.

All five candidates—paper-lookup, scientific-critical-thinking,
scientific-writing, citation-management and peer-review—are **reviewed but not
selected** for this release. Reasons include incompatible output/intake protocols,
broader endpoint/credential behavior, a referenced authenticated Parallel workflow,
and conditional upstream citation instructions. OpenRouter is only an optional
diagram dependency of critical-thinking, not a general hard requirement. The
existing original procedure remains the selected runtime skill. No upstream
content was copied into it, edited or stripped of attribution; no upstream code
was executed or installed. At that selection audit, the
skill/template/license/manifest bytes and the `local-procedures-v1` default bundle
were unchanged. The later `local-procedures-v2` writing revision changes the
original local skill, task template and trusted prompt mode selection; it does
not vendor an upstream candidate. There is no runtime clone or automatic update.

## Runtime integration

Python 3.12+ is required for filesystem junction checks. Offline validation,
prompt loading and schemas use only the standard library. Live requests reuse
existing framework-neutral academic functions and their existing `requests`
dependency; the bridge does not import CrewAI or its BaseTool classes, and does
not modify existing academic tools. No paid service/key is mandatory. Crossref,
PubMed and arXiv are default public adapters; OpenAlex and Semantic Scholar are
opt-in and may have current availability/rate limits. Parallel/OpenRouter are
not implemented or auto-enabled.

```python
from pathlib import Path
from pi_worker.research_tools import (
    BUNDLE_REVISION, BUNDLE_MANIFEST, TASK_TEMPLATE, SKILL_PATH,
    OUTPUT_SCHEMA, REQUIRED_FILES,
    build_research_prompt, validate_research_outputs, validate_workspace,
)

prompt = build_research_prompt(job)  # shared ResearchJob or mapping
validation = validate_research_outputs(Path(attempt_workspace))
if validation["valid"] is not True:
    # Fail this output; never use bool(validation), which is always truthy.
    raise ValueError("research_output_invalid")
```

`validate_workspace` is the same callable as `validate_research_outputs`.
`build_research_prompt(job)` includes only topic/mode/task_id/attempt, omits
user/email/credentials and encodes angle delimiters in the untrusted job JSON.
It selects a writing contract from the validated `brief`/`review` enum and puts
that trusted contract outside the job JSON. Template substitution happens once,
so mode-like text, closing delimiters and template placeholders inside a topic
remain research data. This boundary is not evidence that a real model always
resists semantic prompt injection. It reads immutable repository resources,
not user-selected paths. The runtime
should discover only `SKILL_PATH` and the pinned manifest. Supply the immutable
application checkout on PYTHONPATH when invoking tools from the isolated attempt
workspace; do not add control credentials or a private Python HOME. The controller
must terminate/fence the producing attempt before validation/copying to avoid
file replacement races. Source text and model instructions cannot grant access.

## Writing modes in v2

| Mode | Expected writing |
| --- | --- |
| `brief` | A compact answer, the most relevant evidence and its limits. Concise prose or useful bullets may support a bounded answer; a catalogue of papers does not replace synthesis. |
| `review` | A narrative literature review with an abstract, an introduction that frames the question, scope and concise methods, thematic findings, discussion, limitations, a bounded conclusion and references. |

Both modes first plan an argument from the material actually acquired. In a
review, themes follow the question, mechanisms, comparisons or outcomes rather
than assigning each paper its own Findings subsection. Comparable studies are
discussed together: what agrees, what differs, and which differences in methods,
conditions or measures are supported by the available material. Unknown reasons
remain unknown; incompatible metrics do not justify a ranking or pooled effect.
Published findings and the review author's interpretation must be distinguishable.
Tables and lists can supplement the main argument.

Methods summarize the actual search scope, criteria and important access limits
in readable prose. Exact queries, individual screening decisions and detailed
audit records stay in the required evidence artifacts. No fixed word, paragraph
or paper count is imposed, and a paragraph need not cite two papers. Sparse or
abstract-only evidence narrows the answer; it must not be padded with invented
comparisons or inferred full-text details. An unanswerable question requires an
honest evidence-insufficiency report. Neither mode claims systematic-review status.

## CLI and function interface

Run from the application checkout, or with that checkout on PYTHONPATH:

```bash
python -m pi_worker.research_tools schema
python -m pi_worker.research_tools search --query "quantum error correction" --provider crossref --limit 10
python -m pi_worker.research_tools verify --kind doi --identifier "10.1234/example"
python -m pi_worker.research_tools validate --workspace /assigned/attempt/workspace
```

The example DOI is illustrative and is not asserted to exist. `search` and
`verify` make network requests when explicitly invoked; `schema` and `validate`
do not. Repeat `--provider` to select multiple distinct providers. Limits are
1..50 observed records per adapter, query length 1..2000 characters. CLI stdout
is JSON; exit 0 means the operation met its structural condition, 2 means invalid
input/output, 3 means every search source failed or verification was not verified.
Partial source failure retains records from working sources and returns exit 0
with explicit errors in the JSON. Never infer verification from exit 0 on search.
Provider HTTP functions retain their existing bounded retries/timeouts; the
worker's total deadline and process cancellation still apply.

[bridge.py](../pi_worker/research_tools/bridge.py) exposes:

- `search(query, *, providers=None, limit=10) -> dict`: `schema_version`, `searches`,
  `papers`, `database_total=null`, `notes`. There is one log record for each exact
  provider/query adapter invocation; PubMed internally uses search/fetch requests.
  There is no hidden query expansion. `result_count` counts observed returned
  rows before cross-provider deduplication, never a database total. Failure count
  is null, raw exception messages are redacted, and every failure is retained.
- `normalize_paper(record, timestamp=None) -> dict`: stable paper ID, metadata,
  sources, actual observed availability and initially unknown verification.
  A PDF URL/open-access flag never sets acquired full-text availability.
- `verify_identifier(kind, value) -> dict`: fixed-endpoint DOI/Crossref or
  PMID/NCBI metadata lookup; returns identifier, metadata and a verification
  object. Only exact identifier equality is `verified`. A mismatched identifier
  is `failed`; request errors/missing metadata are `unknown`. This is metadata
  verification, not paper authenticity, full-text access or scientific validity.

## Output schemas and validation

The authoritative record definitions are in
[schemas.py](../pi_worker/research_tools/schemas.py); `schema` prints them as JSON
Schema fragments plus report/validation specifications. The parent may import
`OUTPUT_SCHEMA` into the shared protocol without duplicating it. All record
fields are required, nullable fields explicitly use null, unknown fields are
rejected, and timestamps are UTC RFC3339 ending in Z.

| Producer file | Required content |
| --- | --- |
| searches.jsonl | source, query, searched_at, success/partial/error status, observed result_count or null, error or null |
| papers.jsonl | paper_id, title, authors array, year/null, doi/pmid/url/null, sources, retrieved_at, metadata/abstract/full_text availability with acquired timestamps, abstract/null, full_text_path/null, verification |
| screening.jsonl | paper_id, include/exclude/uncertain decision, reason, nonempty criteria array |
| evidence.jsonl | evidence_id, paper_id, finding, method, limitations, metadata/abstract/full_text access_level, locator |
| claims.jsonl | claim_id, exact HTML claim marker, evidence_ids, supported/conflicting/insufficient assessment, explanation |
| references.json | array of unique positive n/paper_id mappings; [] for none |
| report.md | Scope, Methods, Findings, Limitations and References headings (Chinese equivalents allowed), literal numbered [n] citations and matching claim markers |

The report row describes the validator's structural minimum. The `review`
writing contract also calls for the narrative structure described above; passing
the structural checks does not prove that a report develops that argument.

Empty JSONL means a zero-byte file, not `[]` or blank lines. All files are UTF-8
without BOM. JSON duplicate keys, nonfinite numbers, malformed types, unsupported
fields and invalid timestamps fail. Each paper needs one screening record;
evidence must refer to included papers and an acquired access level. Claims
must refer to existing evidence and cite its papers within the claim paragraph.
One coherent synthesis paragraph can link several evidence records and papers
through a single claim's `evidence_ids`. Its exact `<!-- claim:CLAIM_ID -->`
comment stays immediately before that paragraph, with no intervening heading or
other claim comment; each supporting paper is cited in the same paragraph.
This permits cross-paper synthesis without turning every source into a separate
sentence, bullet or subsection.
Conflicting assessments need at least two distinct evidence records. References
cannot have duplicate numbers or assign multiple numbers to the same paper.
Grouped/ranged citations are rejected; use separate [1] [2]. Every cited number
must resolve. Full-text evidence requires a nonempty safely located acquired
text file; actual source authenticity is not established offline.

The validator return shape is always:

```json
{
  "validator_version": "research-output/1.0.0",
  "valid": false,
  "checks": [{"name": "check_name", "status": "pass", "detail": "explanation"}],
  "errors": [],
  "warnings": [],
  "provenance": {}
}
```

The example is a shape illustration, not an accepted validation record. `valid`
is true only if `errors` is empty after all checks. `checks` statuses are
pass/fail/unknown; unknown external verification is a warning, never fabricated
success. On parsed outputs provenance contains validator_version,
search_started_at/search_finished_at (observed request timestamp range, nullable)
and source_verification=`offline_not_repeated`; early failures leave {}. Actual
Pi/Node/model/image provenance must come from the controller/runtime, not this
validator or model-generated files. Controller-owned validation.json and
manifest.json are excluded from producer-required files; B owns their generation,
immutable artifact copying, canonical result digest, and download whitelist.

The validator reads only fixed required files and explicitly declared acquired
full-text paths, rejects traversal/encoding, symlinks, junctions, hard-linked
files, out-of-workspace paths and oversized outputs. It does not recursively
inspect arbitrary directories. It enforces 2 MiB report and 50 MiB observed
required/full-text limits. B must independently safe-enumerate, check total
quotas and rehash every published artifact, then persist validation and manifest;
these checks cannot substitute for a stopped producer and secure controller copy.

Zero evidence can pass only as an explicit evidence-insufficiency report without
supported claims. Source errors and unknown verification must be disclosed;
abstract-only evidence needs an abstract locator and a report limitation. Some
explicit full-text-access phrases and numeric English screening-count phrases
are checked for contradictions with records. Arbitrary scientific prose,
paraphrases, every implicit screening denominator, systematic-review protocol
quality and the truth of claimed acquisition/verification require human/source
review. Systematic-review wording is marked unknown and is never certified by
this validator. The validator does not assess narrative coherence, depth of
synthesis or whether the discussion answers the research question. Those require
reading the report and checking its important claims against acquired source
material. No fixture pass proves scientific accuracy or public API reachability.

## Local validation evidence

```bash
python -m unittest discover -s tests/scientific_pi/research -v
```

The scoped suite uses explicitly synthetic controlled
[fixtures](../tests/scientific_pi/research/fixtures.py) and mocked providers.
It covers zero evidence, abstract-only access, missing/duplicate references,
claim/evidence links and paragraph citations, duplicate papers, contradictory
evidence, source errors, unknown/mismatched identifier verification, malformed
schemas/JSON/UTF-8/timestamps, quota/path/link checks, CLI exits, prompt boundaries,
local bundle hashes and import independence from CrewAI. The v2 regressions also
check distinct trusted writing contracts, rejection of unknown or malformed
modes, malicious topic delimiters/placeholders that cannot replace that contract,
and one adjacent claim paragraph supported by two papers with both citations.
It makes no live model or source calls and does not validate real scientific
findings.

As of the **2026-10-23 v2 candidate verification**, the research suite passed
**51 tests**. The ARM64 candidate also passed **6 JSONL compactor tests**,
**7 Linux isolation tests**, **1 actual pinned Pi CLI test using a loopback model
fixture**, dependency consistency checks and resource/hash pins. Its image digest
was `sha256:bf125403086dd6d5e82174550a28e29457b30604c37fd4d239661448231016c4`.
These are controlled compatibility and containment checks. The loopback fixture
does not establish that a real model writes a good narrative review, retrieves
adequate literature or interprets it accurately.

A real `review` task, `1e5cec61-23d5-5a68-a658-82a9b168de2d`, then completed
the chip-manufacturing question for 2020–2026 on the deployed v2 image. Its report,
eight artifacts, manifest and full result digest were verified; all 15 included
evidence records were abstract-only. An independent anonymous review rated the
previous report 14/24 (revise) and the v2 report 18/24 (acceptable as a focused
narrative review based on abstracts), checking seven representative claims
against the saved abstracts. It recommended local wording edits; no full text,
independent bibliographic authenticity or manufacturing maturity was verified.

The generated online report retains its original bytes. A separate editorial
copy clarifies gas/plasma steps, qualitative yield, unsupported temporal wording
and the limits of cross-study comparisons. The acceptance record is in the parent
repository at `docs/scientific-research-pi/review-quality-20261023/`. This single
same-question run used a different retrieved evidence set, so it is not a
controlled writing experiment or proof of quality for other questions. The v2
prompt change and a green consistency validator alone do not constitute that
acceptance.
