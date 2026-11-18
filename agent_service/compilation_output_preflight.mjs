// Deterministic author feedback only. No source execution, I/O, repair or prose.
// Python's stopped-output collector remains the authoritative final validator.
import { createHash } from 'node:crypto';

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const keys = (value, names) => object(value) && Object.keys(value).sort().join('\0') === [...names].sort().join('\0');
const size = value => Buffer.byteLength(value, 'utf8');
const chars = value => [...value].length;
const sha = value => createHash('sha256').update(value).digest('hex');
const text = (value, max = 400) => typeof value === 'string' && value.trim().length > 0 && chars(value) <= max
  && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u.test(value);
const identity = value => text(value) && !/[\u0000-\u001f]/u.test(value);
const engines = new Set(['html-svg', 'canvas', 'three']);
const namespaces = new Set(['http://www.w3.org/2000/svg', 'http://www.w3.org/1999/xlink',
  'http://www.w3.org/XML/1998/namespace', 'http://www.w3.org/2000/xmlns/',
  'http://www.w3.org/1999/xhtml', 'http://www.w3.org/1998/Math/MathML']);
const remote = value => [...value.matchAll(/(?:https?|ftp|file):\/\/[^\s<>"'`)\]}]+|\b(?:www\.|space\.bilibili\.com)[^\s<>"'`)\]}]*/gi)]
  .some(match => !namespaces.has(match[0]));
const codepointCompare = (a, b) => {
  const x = [...a], y = [...b];
  for (let i = 0; i < Math.min(x.length, y.length); i++) {
    const d = x[i].codePointAt(0) - y[i].codePointAt(0); if (d) return d;
  }
  return x.length - y.length;
};
const canonical = value => {
  if (Array.isArray(value)) return '[' + value.map(canonical).join(',') + ']';
  if (object(value)) return '{' + Object.keys(value).sort(codepointCompare)
    .map(key => JSON.stringify(key) + ':' + canonical(value[key])).join(',') + '}';
  return JSON.stringify(value);
};
const portable = value => value === null || ['string', 'boolean'].includes(typeof value)
  || typeof value === 'number' && Number.isSafeInteger(value)
  || Array.isArray(value) && value.every(portable)
  || object(value) && Object.values(value).every(portable);
const fold = value => value.toLowerCase().replaceAll('ß', 'ss').replaceAll('ς', 'σ');
const validPath = name => typeof name === 'string' && chars(name) >= 1 && chars(name) <= 500
  && !name.startsWith('/') && !/[\\\u0000-\u001f\u007f]/u.test(name)
  && name.split('/').every(part => part.length > 0 && chars(part) <= 120 && !['.', '..'].includes(part)
    && !/[\\:\u0000-\u001f"<>|?*]/u.test(part) && !/[ .]$/.test(part)
    && !/^(con|prn|aux|nul|com[1-9]|lpt[1-9])$/i.test(part.split('.')[0]));
const DRAFT = ['skill_id', 'version', 'kind', 'files', 'runtime_requirements'];
const PACKAGE = ['schema', ...DRAFT, 'name', 'description', 'files_sha256', 'sha256'];
const TOP = ['schema', 'input_sha256', 'snapshot_sha256', 'evidence_scope', 'summary',
  'packages', 'index', 'exclusions', 'limitations'];
const LIMITATION = 'No new video, motion or audio validation was performed by this compilation.';

function frontmatter(source) {
  if (!source.startsWith('---\n') && !source.startsWith('---\r\n')) return null;
  const lines = source.split(/\r\n|[\n\r\v\f\u001c-\u001e\u0085\u2028\u2029]/u);
  const end = lines.indexOf('---', 1), fields = {};
  if (end < 0 || !lines.slice(end + 1).join('\n').trim()) return null;
  for (const line of lines.slice(1, end)) {
    const match = /^(name|description):[ \t]*(.+)$/.exec(line);
    if (!match || Object.hasOwn(fields, match[1])) return null;
    let value = match[2].trim();
    if (value.startsWith('"')) { try { value = JSON.parse(value); } catch { return null; } }
    else if (value.startsWith("'")) {
      if (value.length < 2 || !value.endsWith("'")) return null;
      value = value.slice(1, -1).replaceAll("''", "'");
    }
    if (typeof value !== 'string' || /[\n\r]/.test(value)) return null;
    fields[match[1]] = value;
  }
  return keys(fields, ['name', 'description']) && /^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(fields.name)
    && fields.name.length < 64 && text(fields.description, 1200) ? fields : null;
}

export function inspectCompilationOutput(value, document, inputSha256) {
  const errors = []; let omitted = 0;
  const fail = (path, code) => { if (errors.length < 64) errors.push({ path, code }); else omitted++; };
  const need = (condition, path, code) => { if (!condition) fail(path, code); return !!condition; };
  const total = Array.isArray(document?.records) ? document.records.length : 0;
  const used = new Set(), excluded = new Set(); let bundleBytes = 0, bundleFiles = 1;
  if (!keys(value, TOP)) {
    fail('$', 'exact_top_level_fields_required');
    if (object(value)) {
      for (const key of TOP) if (!Object.hasOwn(value, key)) fail('$.' + key, 'required_field_missing');
      for (const key of Object.keys(value)) if (!TOP.includes(key)) fail('$', 'unexpected_top_level_field');
    }
  }
  if (!object(value)) return { valid: false, errors, errors_truncated: false, counts: { records: total } };
  need(value.schema === 'video.animation-wikiskill-result.v2', '$.schema', 'compact_result_schema_required');
  need(value.input_sha256 === inputSha256, '$.input_sha256', 'exact_input_hash_required');
  need(value.snapshot_sha256 === document?.snapshot?.sha256, '$.snapshot_sha256', 'exact_snapshot_hash_required');
  need(value.evidence_scope === 'frozen_records_only_no_new_media_validation', '$.evidence_scope', 'fixed_evidence_scope_required');
  need(text(value.summary, 16000), '$.summary', 'nonempty_summary_max_16000');
  const packages = Array.isArray(value.packages) ? value.packages : [];
  need(Array.isArray(value.packages) && packages.length >= 1 && packages.length <= 256, '$.packages', 'package_count_1_to_256');
  const byId = new Map(), names = new Set();
  for (const [i, row] of packages.entries()) {
    const p = '$.packages[' + i + ']';
    if (!need(object(row), p, 'package_object_required')) continue;
    const draft = keys(row, DRAFT), normalized = keys(row, PACKAGE);
    need(draft || normalized, p, 'exact_draft_or_normalized_package_fields_required');
    need(identity(row.skill_id), p + '.skill_id', 'bounded_skill_identity_required');
    need(row.version === 1, p + '.version', 'package_version_must_be_1');
    need(['foundation', 'expression'].includes(row.kind), p + '.kind', 'foundation_or_expression_required');
    if (byId.has(row.skill_id)) fail(p + '.skill_id', 'duplicate_skill_id');
    byId.set(row.skill_id, row);
    const files = object(row.files) ? row.files : {};
    const fileNames = Object.keys(files), folded = new Set(), hashes = {};
    need(object(row.files) && fileNames.length >= 1 && fileNames.length <= 32 && Object.hasOwn(files, 'SKILL.md'), p + '.files', 'one_to_32_files_including_SKILL_md');
    let packageBytes = 0;
    for (const name of fileNames) {
      // Never reflect arbitrary submitted filenames or prose in diagnostics.
      const fp = p + '.files[' + fileNames.indexOf(name) + ']';
      need(validPath(name) && (name === 'SKILL.md' || /^(references|assets|scripts)\//.test(name)), fp, 'portable_skill_relative_path_required');
      need(!folded.has(fold(name)), fp, 'casefold_path_collision'); folded.add(fold(name));
      const content = files[name];
      if (!need(text(content, 131072), fp, 'nonempty_portable_text_max_131072_characters')) continue;
      const bytes = size(content); packageBytes += bytes; hashes[name] = sha(content);
      need(bytes <= 131072, fp, 'file_max_131072_bytes');
      need(!remote(content), fp, 'external_dependency_forbidden');
      for (const match of content.matchAll(/\[[^\]]*\]\(([^\s)]+)(?:\s+[^)]*)?\)/g)) {
        const target = match[1].split('#', 1)[0]; if (!target) continue;
        if (!need(!target.startsWith('/') && !/[\\?]/.test(target), fp, 'local_reference_required')) continue;
        const parts = name.split('/').slice(0, -1); let invalid = false;
        for (const part of target.split('/')) {
          if (part === '..') { if (!parts.length) invalid = true; else parts.pop(); }
          else if (part !== '.') parts.push(part);
        }
        need(!invalid && Object.hasOwn(files, parts.join('/')), fp, 'local_reference_missing_or_escaping');
      }
    }
    for (const name of fileNames) {
      const parts = name.split('/');
      for (let j = 1; j < parts.length; j++) need(!folded.has(fold(parts.slice(0, j).join('/'))), p + '.files', 'file_shadows_directory');
    }
    need(packageBytes <= 1048576, p + '.files', 'package_max_1048576_bytes');
    bundleBytes += packageBytes; bundleFiles += fileNames.length + 1;
    const meta = typeof files['SKILL.md'] === 'string' ? frontmatter(files['SKILL.md']) : null;
    need(meta !== null, p + '.files.SKILL.md', 'exact_name_description_frontmatter_and_body_required');
    if (meta) { need(!names.has(meta.name), p, 'duplicate_skill_name'); names.add(meta.name); }
    const runtime = row.runtime_requirements;
    need(object(runtime) && Array.isArray(runtime.engines) && runtime.engines.length > 0
      && runtime.engines.every(e => engines.has(e)) && new Set(runtime.engines).size === runtime.engines.length,
      p + '.runtime_requirements.engines', 'unique_supported_engines_required');
    need(portable(runtime) && size(canonical(runtime)) <= 8192 && !remote(canonical(runtime)),
      p + '.runtime_requirements', 'portable_runtime_without_external_dependency_max_8192_bytes');
    if (normalized && meta) {
      const normalizedBody = { schema: 'video.animation-skill-package.v1', skill_id: row.skill_id,
        version: row.version, kind: row.kind, ...meta, files, runtime_requirements: runtime, files_sha256: hashes };
      need(row.schema === normalizedBody.schema && row.name === meta.name && row.description === meta.description
        && canonical(row.files_sha256) === canonical(hashes) && row.sha256 === sha(canonical(normalizedBody)), p, 'normalized_package_hash_or_metadata_mismatch');
    }
  }
  need(bundleBytes <= 33554432 && bundleFiles <= 1024, '$.packages', 'whole_bundle_size_or_file_limit');
  const ranges = (pairs, p, destination, unique = false) => {
    if (!need(Array.isArray(pairs) && pairs.length > 0, p, 'nonempty_half_open_range_list_required')) return;
    let previous = -1;
    for (const [i, pair] of pairs.entries()) {
      if (!need(Array.isArray(pair) && pair.length === 2 && pair.every(Number.isSafeInteger)
        && 0 <= pair[0] && pair[0] < pair[1] && pair[1] <= total && pair[0] >= previous,
        p + '[' + i + ']', 'ordered_nonoverlapping_in_bounds_integer_range_required')) continue;
      previous = pair[1];
      for (let n = pair[0]; n < pair[1]; n++) {
        if (unique && destination.has(n)) fail(p + '[' + i + ']', 'duplicate_excluded_record');
        destination.add(n);
      }
    }
  };
  const index = Array.isArray(value.index) ? value.index : [], parents = new Map();
  need(Array.isArray(value.index) && index.length === packages.length, '$.index', 'one_index_row_per_package_required');
  for (const [i, row] of index.entries()) {
    const p = '$.index[' + i + ']';
    if (!need(keys(row, ['skill_id', 'parent_skill_id', 'source_record_ranges']), p, 'exact_skill_id_parent_skill_id_source_record_ranges_fields_required')) continue;
    need(typeof row.skill_id === 'string' && byId.has(row.skill_id) && !parents.has(row.skill_id), p + '.skill_id', 'unique_existing_skill_id_required');
    need(row.parent_skill_id === null || typeof row.parent_skill_id === 'string'
      && byId.has(row.parent_skill_id) && row.parent_skill_id !== row.skill_id, p + '.parent_skill_id', 'null_or_other_existing_parent_required');
    parents.set(row.skill_id, row.parent_skill_id);
    ranges(row.source_record_ranges, p + '.source_record_ranges', used);
  }
  const roots = [...parents].filter(([, parent]) => parent === null).map(([id]) => id);
  need(roots.length === 1 && byId.get(roots[0])?.kind === 'foundation', '$.index', 'one_foundation_root_required');
  for (const id of parents.keys()) {
    const seen = new Set(); let current = id;
    while (current !== null && parents.has(current)) {
      if (seen.has(current)) { fail('$.index', 'hierarchy_cycle'); break; }
      seen.add(current); current = parents.get(current);
    }
  }
  need(Array.isArray(value.exclusions) && value.exclusions.length <= total, '$.exclusions', 'bounded_exclusions_list_required');
  for (const [i, row] of (Array.isArray(value.exclusions) ? value.exclusions : []).entries()) {
    const p = '$.exclusions[' + i + ']';
    if (!need(keys(row, ['record_ranges', 'reason']), p, 'exact_record_ranges_reason_fields_required')) continue;
    need(text(row.reason, 4000), p + '.reason', 'nonempty_reason_max_4000');
    ranges(row.record_ranges, p + '.record_ranges', excluded, true);
  }
  need(![...used].some(n => excluded.has(n)), '$.index', 'used_and_excluded_records_must_not_overlap');
  const covered = new Set([...used, ...excluded]);
  need(covered.size === total, '$.exclusions', 'every_frozen_record_must_be_used_or_excluded');
  need(Array.isArray(value.limitations) && value.limitations.length >= 1 && value.limitations.length <= 128
    && value.limitations.includes(LIMITATION) && value.limitations.every(s => text(s, 4000)),
    '$.limitations', 'bounded_limitations_including_fixed_observation_scope_required');
  need(size(canonical(value)) <= 16777216, '$', 'output_max_16777216_bytes');
  return { valid: errors.length === 0 && omitted === 0, errors, errors_truncated: omitted > 0,
    counts: { records: total, packages: packages.length, used: used.size, excluded: excluded.size,
      uncovered: Math.max(0, total - covered.size), bundle_bytes: bundleBytes, bundle_files: bundleFiles } };
}
