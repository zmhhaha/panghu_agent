# Whole-library WikiSkill compilation contract

You are compiling one immutable snapshot, not creating a video, accepting a
method or publishing anything yourself. The trusted host owns publication.

Read `inputs/animation-wikiskill-input.json`. Its `snapshot` header identifies
the exact source snapshot. Each `records` entry names an immutable local JSON
file with a record ID, kind, content SHA-256, file SHA-256 and byte count. Read
the actual files as needed, keeping their scope and provenance. No source video
or sound has been supplied for new observation. Existing recorded observations
are evidence with their original limitations; stored prompts are intent.

Write exactly one JSON object to `output.json`:

```
{
  "schema": "video.animation-wikiskill-result.v1",
  "input_sha256": "the supplied task input digest",
  "snapshot_sha256": "snapshot.sha256 from the manifest",
  "evidence_scope": "frozen_records_only_no_new_media_validation",
  "summary": "What was organized, deduplicated and qualified in this release",
  "packages": [
    {
      "skill_id": "stable-semantic-identity",
      "version": 1,
      "kind": "foundation",
      "files": {
        "SKILL.md": "---\nname: semantic-entry-name\ndescription: When to use this entry.\n---\nSelf-contained instructions authored from the supplied evidence."
      },
      "runtime_requirements": {"engines": ["html-svg", "canvas", "three"]}
    }
  ],
  "index": [
    {"skill_id": "stable-semantic-identity", "parent_skill_id": null,
     "source_record_ids": ["an actual supplied record ID"]}
  ],
  "exclusions": [{"record_id": "an actual unused record ID", "reason": "Concrete reason"}],
  "limitations": ["No new video, motion or audio validation was performed by this compilation."]
}
```

Schema field names and fixed contract values, including `schema` and
`evidence_scope`, are mandatory. The sample's topics, Skill text and semantic
identifiers are illustrative. Author the actual prose, organization and
technical instructions yourself.

`limitations` MUST include this exact string as a standalone array element:

```
"No new video, motion or audio validation was performed by this compilation."
```

Copy it byte for byte, including punctuation, as its own element. Do not
translate, paraphrase or append another sentence within that element. Put any
additional explanation in separate array elements. A semantically equivalent
or longer combined sentence does not satisfy this fixed marker. The host
rejects invalid output; it never inserts or rewrites this statement for you.

The host computes standard Skill-package frontmatter metadata and byte hashes.

Use at most 256 packages. Each package has at most 32 UTF-8 files, at most
128 KiB per file and 1 MiB total. Entire result is at most 16 MiB. `SKILL.md` is
mandatory; other files must be under `references/`, `assets/` or `scripts/`.
Use `kind` foundation or expression. Every package has `version: 1` within this
whole-library release; later releases are distinct immutable releases.
Only HTML/SVG (`html-svg`), Canvas (`canvas`) and Three (`three`) engines are
supported. Declare only actual applicability; do not invent runtime support.

Every package has exactly one index entry and nonempty actual source-record
links. The index is a tree with one foundation root; other entries point to an
existing parent. All supplied record IDs must occur in either source links or
exclusions; the two sets cannot overlap. Deduplicate repeated evidence and
explain exclusions rather than silently dropping hard or contradictory cases.

Use short discovery descriptions. Put overview and routing in the root,
reusable principles in focused children, and detailed material in local files.
Within a package, Markdown links must resolve to existing local files. To route
across packages, name the precise skill ID and explain using `skill_search` then
`skill_read`; do not create broken cross-package Markdown links. Operational
files must contain no external URLs, reference-video dependency or instruction
to fetch the original source. Namespace identifiers used by SVG/XML are inert.

No quality score, fabricated review, acceptance, visual pass or unsupported
claim of having watched video/audio. Preserve uncertainty, counterexamples,
conflicting feedback and unverified applicability. Do not inherit a previous
Wiki or old method as authority: derive and qualify every current instruction
from this snapshot's real material. Source IDs belong in the index, so runtime
Skills remain useful without private raw records or a live original video.
