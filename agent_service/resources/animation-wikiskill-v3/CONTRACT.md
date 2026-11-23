# Frozen evidence to WikiSkill: compact compiler wire format

Only `read_compilation_input` and `write` are available. Start with mode `survey`,
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
