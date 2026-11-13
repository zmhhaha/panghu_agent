// New, separately admitted tools. The frozen read_inputs extension stays intact.
// Nothing here executes a skill, follows a URL, or accepts a filesystem root.
import { constants } from 'node:fs';
import { lstat, open, realpath } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';

const PACKAGE = 'video.animation-skill-package.v1';
const CATALOG = 'video.animation-skill-catalog.v1';
const MANIFEST = 'video.animation-skill-files.v1';
const SEARCH = 'video.animation-skill-search.v1';
const READ = 'video.animation-skill-read.v1';
const MAX_FILE = 128 * 1024;
const MAX_PACKAGE = 1024 * 1024;
const decoder = new TextDecoder('utf-8', { fatal: true });
const shaPattern = /^[0-9a-f]{64}$/;
const engines = new Set(['html-svg', 'canvas', 'three']);
const kinds = new Set(['foundation', 'expression']);
const fail = (condition) => { if (!condition) throw new Error('animation_skill_tool_invalid'); };
const abort = (signal) => { if (signal?.aborted) throw new Error('animation_skill_tool_cancelled'); };
const record = (value) => value !== null && typeof value === 'object' && !Array.isArray(value);
const checksum = (bytes) => createHash('sha256').update(bytes).digest('hex');

// Python's canonical_bytes orders Unicode codepoints, not UTF-16 code units.
function compare(left, right) {
  const a = Array.from(left, c => c.codePointAt(0)), b = Array.from(right, c => c.codePointAt(0));
  for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
  return a.length - b.length;
}
export function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`;
  if (record(value)) return `{${Object.keys(value).sort(compare).map(k => `${JSON.stringify(k)}:${canonical(value[k])}`).join(',')}}`;
  fail(value === null || typeof value === 'string' || typeof value === 'boolean'
    || typeof value === 'number' && Number.isSafeInteger(value));
  return JSON.stringify(value);
}
const digest = (value) => checksum(Buffer.from(canonical(value), 'utf8'));
const withoutSha = (value) => Object.fromEntries(Object.entries(value).filter(([k]) => k !== 'sha256'));
const same = (left, right) => canonical(left) === canonical(right);
const exactKeys = (value, names) => record(value) && same(Object.keys(value).sort(), [...names].sort());

function relative(name) {
  fail(typeof name === 'string' && name.length > 0 && name.length <= 500 && !name.includes('\\'));
  const parts = name.split('/');
  fail(parts.every(p => p && p !== '.' && p !== '..' && p.length <= 120
    && !/[\u0000-\u001f\u007f:"<>|?*]/u.test(p) && !/[ .]$/.test(p)
    && !/^(?:con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\.|$)/i.test(p)));
  return parts;
}

async function readFile(root, name, maximum, signal) {
  abort(signal);
  const parts = relative(name);
  let current = root;
  const rootInfo = await lstat(root);
  fail(rootInfo.isDirectory() && !rootInfo.isSymbolicLink() && await realpath(root) === root);
  for (const part of parts.slice(0, -1)) {
    current = path.join(current, part);
    const info = await lstat(current);
    fail(info.isDirectory() && !info.isSymbolicLink() && await realpath(current) === current);
  }
  const file = path.join(current, parts.at(-1));
  const before = await lstat(file);
  fail(before.isFile() && !before.isSymbolicLink() && before.nlink === 1
    && before.size > 0 && before.size <= maximum);
  const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  let raw;
  try {
    const opened = await handle.stat();
    fail(opened.isFile() && opened.dev === before.dev && opened.ino === before.ino
      && opened.nlink === 1 && opened.size === before.size);
    const buffer = Buffer.alloc(before.size + 1);
    let used = 0;
    while (used < buffer.length) {
      abort(signal);
      const { bytesRead } = await handle.read(buffer, used, buffer.length - used, used);
      if (!bytesRead) break;
      used += bytesRead;
    }
    raw = buffer.subarray(0, used);
    const after = await handle.stat();
    fail(raw.length === before.size && after.size === before.size
      && after.mtimeMs === before.mtimeMs && after.ctimeMs === before.ctimeMs);
  } finally {
    await handle.close();
  }
  abort(signal);
  return raw;
}

function parse(raw) {
  const text = decoder.decode(raw), value = JSON.parse(text);
  fail(canonical(value) === text);
  return value;
}

async function catalog(signal) {
  // The new profile stages a protected directory beside its main input file.
  // No tool parameter or model-controlled root changes this location.
  const root = path.resolve(process.cwd(), 'inputs', 'animation-skills');
  const value = parse(await readFile(root, 'catalog.json', MAX_PACKAGE, signal));
  fail(exactKeys(value, ['schema', 'recipient_owner', 'consumer_id', 'task_sha256',
    'allowed_engines', 'skills', 'policy', 'evidence_scope', 'sha256'])
    && value.schema === CATALOG && shaPattern.test(value.sha256)
    && digest(withoutSha(value)) === value.sha256 && shaPattern.test(value.task_sha256)
    && typeof value.recipient_owner === 'string' && typeof value.consumer_id === 'string'
    && Array.isArray(value.allowed_engines) && value.allowed_engines.length > 0
    && value.allowed_engines.every(v => engines.has(v))
    && Array.isArray(value.skills) && value.skills.length <= 256
    && value.evidence_scope === 'frozen_published_discovery_not_agent_read_or_application');
  const ids = new Set(), versions = new Set(), packages = new Set();
  for (const row of value.skills) {
    fail(exactKeys(row, ['skill_id', 'version', 'kind', 'name', 'description', 'package_sha256',
      'method_version_id', 'method_content_sha256', 'publication_sha256', 'runtime_requirements'])
      && typeof row.skill_id === 'string' && Number.isSafeInteger(row.version) && row.version > 0
      && kinds.has(row.kind) && typeof row.name === 'string' && typeof row.description === 'string'
      && shaPattern.test(row.package_sha256) && shaPattern.test(row.method_content_sha256)
      && shaPattern.test(row.publication_sha256) && record(row.runtime_requirements)
      && Array.isArray(row.runtime_requirements.engines)
      && row.runtime_requirements.engines.some(v => value.allowed_engines.includes(v))
      && !ids.has(row.method_version_id) && !versions.has(`${row.skill_id}\0${row.version}`)
      && !packages.has(row.package_sha256));
    ids.add(row.method_version_id);
    versions.add(`${row.skill_id}\0${row.version}`);
    packages.add(row.package_sha256);
  }
  return { root, value };
}

function binding(value, schema) {
  return { schema, snapshot_sha256: value.sha256, task_sha256: value.task_sha256,
    recipient_owner: value.recipient_owner, consumer_id: value.consumer_id };
}

function tokens(text) {
  const value = text.toLowerCase();
  const result = new Set(value.match(/[a-z0-9][a-z0-9_-]*/g) || []);
  for (const found of value.match(/[\u3400-\u9fff]+/g) || []) {
    for (let i = 0; i < Math.max(1, found.length - 1); i++) result.add(found.slice(i, i + 2));
  }
  return result;
}

export async function searchSkills(params, signal) {
  fail(record(params) && Object.keys(params).every(k => ['query', 'kind', 'engine', 'offset', 'limit'].includes(k)));
  const { query = '', kind = null, engine = null, offset = 0, limit = 32 } = params;
  fail(typeof query === 'string' && query.length <= 512 && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/u.test(query)
    && (kind === null || kinds.has(kind)) && (engine === null || engines.has(engine))
    && Number.isSafeInteger(offset) && offset >= 0 && Number.isSafeInteger(limit) && limit >= 1 && limit <= 64);
  const { value } = await catalog(signal);
  const terms = tokens(query), ranked = [];
  for (const row of value.skills) {
    if (kind !== null && row.kind !== kind || engine !== null && !row.runtime_requirements.engines.includes(engine)) continue;
    const words = tokens(`${row.name} ${row.description} ${row.skill_id}`);
    const score = [...terms].filter(v => words.has(v)).length;
    if (terms.size && !score) continue;
    ranked.push({ row, score });
  }
  ranked.sort((a, b) => b.score - a.score || compare(a.row.skill_id, b.row.skill_id) || a.row.version - b.row.version);
  const selected = ranked.slice(offset, offset + limit).map(v => v.row);
  const receipt = { ...binding(value, SEARCH), query, kind, engine, offset, limit, total: ranked.length,
    next_offset: offset + selected.length < ranked.length ? offset + selected.length : null,
    skills: selected, evidence_scope: 'catalog_discovery_not_skill_read_or_application' };
  receipt.sha256 = digest(receipt);
  abort(signal);
  return { content: [{ type: 'text', text: canonical(receipt) }], details: { receipt } };
}

export async function readSkill(params, signal) {
  fail(record(params) && Object.keys(params).every(k => ['package_sha256', 'path'].includes(k))
    && typeof params.package_sha256 === 'string' && shaPattern.test(params.package_sha256));
  const selectedPath = params.path === undefined ? 'SKILL.md' : params.path;
  relative(selectedPath);
  const { root, value } = await catalog(signal);
  const row = value.skills.find(item => item.package_sha256 === params.package_sha256);
  fail(Boolean(row));
  const directory = `packages/${params.package_sha256}/`;
  const manifest = parse(await readFile(root, directory + 'manifest.json', MAX_FILE, signal));
  fail(exactKeys(manifest, ['schema', 'package_schema', 'skill_id', 'version', 'kind', 'name',
    'description', 'runtime_requirements', 'files_sha256', 'files_bytes', 'sha256'])
    && manifest.schema === MANIFEST && manifest.package_schema === PACKAGE
    && manifest.sha256 === params.package_sha256 && record(manifest.files_sha256)
    && record(manifest.files_bytes) && same(Object.keys(manifest.files_sha256).sort(), Object.keys(manifest.files_bytes).sort())
    && Object.hasOwn(manifest.files_sha256, 'SKILL.md')
    && Object.keys(manifest.files_sha256).length <= 32
    && Object.hasOwn(manifest.files_sha256, selectedPath));
  for (const key of ['skill_id', 'version', 'kind', 'name', 'description', 'runtime_requirements']) fail(same(manifest[key], row[key]));
  const files = {};
  let total = 0;
  for (const name of Object.keys(manifest.files_sha256)) {
    fail(name === 'SKILL.md' || /^(?:references|assets|scripts)\//.test(name));
    relative(name);
    const raw = await readFile(root, directory + name, MAX_FILE, signal);
    fail(Number.isSafeInteger(manifest.files_bytes[name]) && raw.length === manifest.files_bytes[name]
      && checksum(raw) === manifest.files_sha256[name]);
    total += raw.length;
    fail(total <= MAX_PACKAGE);
    files[name] = decoder.decode(raw);
  }
  const candidate = { schema: PACKAGE, skill_id: manifest.skill_id, version: manifest.version,
    kind: manifest.kind, name: manifest.name, description: manifest.description,
    files, files_sha256: manifest.files_sha256, runtime_requirements: manifest.runtime_requirements };
  fail(digest(candidate) === row.package_sha256);
  const receipt = { ...binding(value, READ), package_sha256: row.package_sha256,
    skill_id: row.skill_id, version: row.version, path: selectedPath,
    file_sha256: manifest.files_sha256[selectedPath], bytes: manifest.files_bytes[selectedPath],
    evidence_scope: 'returned_exact_bytes_not_visual_application_or_quality' };
  receipt.sha256 = digest(receipt);
  abort(signal);
  return { content: [{ type: 'text', text: files[selectedPath] }], details: { receipt } };
}

export default function (pi) {
  pi.registerTool({
    name: 'skill_search', label: 'Search frozen animation skills',
    description: 'Search the frozen published animation Skill directory by your own query, optional kind or engine, and pagination. Returns descriptions and exact package hashes; it does not load Skill bodies. Empty query browses the directory. No URL, path, execution or network is accepted.',
    parameters: { type: 'object', properties: {
      query: { type: 'string', maxLength: 512 }, kind: { type: 'string', enum: [...kinds] },
      engine: { type: 'string', enum: [...engines] }, offset: { type: 'integer', minimum: 0 },
      limit: { type: 'integer', minimum: 1, maximum: 64 },
    }, additionalProperties: false },
    execute: async (_id, params, signal) => searchSkills(params, signal),
  });
  pi.registerTool({
    name: 'skill_read', label: 'Read an exact animation skill file',
    description: 'Read complete SKILL.md or a named local attachment from an exact package hash returned by the frozen Skill directory. Returns original UTF-8 text and a task/snapshot/package/file hash receipt. This does not execute attachments or grant publication or visual approval.',
    parameters: { type: 'object', properties: {
      package_sha256: { type: 'string', pattern: '^[0-9a-f]{64}$' },
      path: { type: 'string', default: 'SKILL.md' },
    }, required: ['package_sha256'], additionalProperties: false },
    execute: async (_id, params, signal) => readSkill(params, signal),
  });
}
