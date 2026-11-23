# Frozen evidence to WikiSkill: compact compiler wire format

Only `read_compilation_input`, `write`, and `check_compilation_output` are available. Start with mode `survey`,
input_sha256 equal to the frozen task hash, offset 0, limit 128. Follow next_offset
until null. `record_index` is the stable zero-based position in the complete
immutable manifest and addresses `records/NNNNNN.json` (six digits). The directory
omits all substantive prose/code/reviews/feedback/acceptance context; short labels
are not full titles. Original file hashes bind complete records. Directory
coverage does not prove source reading or method understanding. Raw originals remain
available through mode `file` (exact file_name, offset_bytes, max_bytes up to
16384) and mode `batch` (up to 32 exact file_names). Mode `manifest` is also
available. Every response is bounded and explicitly indicates continuation.

Write exactly one JSON object to `output.json`:

```json
{
  "schema": "video.animation-wikiskill-result.v2",
  "input_sha256": "the exact task input hash",
  "snapshot_sha256": "snapshot.sha256 from survey",
  "evidence_scope": "frozen_records_only_no_new_media_validation",
  "summary": "What useful methods were synthesized and what remains limited",
  "packages": [{
    "skill_id": "animation-authoring-guide",
    "version": 1,
    "kind": "foundation",
    "files": {"SKILL.md": "---\nname: animation-authoring-guide\ndescription: When to consult this entry.\n---\nOriginal self-contained guidance."},
    "runtime_requirements": {"engines": ["html-svg", "canvas", "three"]}
  }],
  "index": [{
    "skill_id": "animation-authoring-guide",
    "parent_skill_id": null,
    "source_record_ranges": [[0, 1]]
  }],
  "exclusions": [{"record_ranges": [[1, 3], [5, 6]], "reason": "A concrete shared exclusion reason"}],
  "limitations": ["No new video, motion or audio validation was performed by this compilation.", "State actual partial-reading and observed-evidence limitations here."]
}
```

The example ranges are illustrative, not a coverage suggestion. Each `[start,end)`
includes start and excludes end. Integers only, within the manifest, sorted and
nonoverlapping within each list. Every package has a nonempty range list and
exactly one index row. There must be exactly one foundation root. Children use
its semantic skill_id as parent; the hierarchy is acyclic. A source can support
more than one package, but no used record may also be excluded. Together used
and excluded ranges cover every supplied record. The collector expands the
addresses, computes only package hashes/metadata, and validates the normal
existing release contract. It never writes or fixes your prose.

Package kind is foundation or expression. A package has SKILL.md
with name/description frontmatter; additional local text files are allowed by
the existing Skill contract. Runtime requirements and engine claims must match
the recorded evidence. Keep reference URLs and borrowed material out of runnable
Skill content. Do not claim training, user approval, publication, continuous
motion or sound validation from this offline compilation.

## Required checked completion

Prefer one compact root and 3–5 useful children (4–6 packages total), adapting
the count to the evidence. Do not write redundant packages just to reach a count.

After writing the complete JSON to `output.json`, call
`check_compilation_output` with exactly `{"input_sha256":"the task hash"}`.
It reads only the fixed manifest and your output, reports concrete JSON-path
errors, and never repairs text or writes files. If `valid` is false, you must
correct your own complete output with `write` and call the check again. End the
task only after the most recent check reports `valid: true` for the final file.
Do not alter the file after that successful check. The stopped Python collector
still applies the full authoritative validation and may reject it.

Keep index rows exactly `{skill_id,parent_skill_id,source_record_ranges}`.
Do not put kind, path, use_when, names, or prose in index rows; that guidance
belongs in SKILL.md. Keep exclusion rows exactly `{record_ranges,reason}`;
`record_range` (singular) is invalid. `source_record_ranges` and `record_ranges`
are lists of half-open pairs, for example `[[0,1],[3,6]]`. Do not omit
`snapshot_sha256` or `evidence_scope`. The fixed compilation limitation above
must occur verbatim in the limitations list. These fields are binding data,
not suggested explanatory content.

Each package is a self-contained file root. Markdown links in its files may
point only to files included in that same package, resolved relative to the
current file. Do not use absolute paths, package-prefixed paths, cross-package
links such as `../other-skill/SKILL.md`, query strings, or reference-video URLs.
The hierarchy is carried by index parent_skill_id; mention another Skill by its
semantic ID in plain prose when useful. Put a linked local reference into this
package's references/ directory and include the actual text in files.
