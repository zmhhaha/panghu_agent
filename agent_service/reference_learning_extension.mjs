// Separately admitted progressive reads over one immutable picture task.
import { constants } from 'node:fs';
import { lstat, open, realpath } from 'node:fs/promises';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { readInputs } from './read_inputs_extension.mjs';

const INPUT = 'reference-learning-input.json';
const MAX_DOCUMENT = 2 * 1024 * 1024;
const decoder = new TextDecoder('utf-8', { fatal: true });
const need = value => { if (!value) throw new Error('reference_learning_tool_invalid'); };
const abort = signal => { if (signal?.aborted) throw new Error('reference_learning_tool_cancelled'); };
const plain = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const permitted = (params, keys) => plain(params) && Object.keys(params).every(key => keys.includes(key));

async function frozenTask(signal) {
  abort(signal);
  const root = path.resolve(process.cwd(), 'inputs');
  const directory = await lstat(root);
  need(directory.isDirectory() && !directory.isSymbolicLink() && await realpath(root) === root);
  const file = path.join(root, INPUT), before = await lstat(file);
  need(before.isFile() && !before.isSymbolicLink() && before.nlink === 1
    && before.size > 0 && before.size <= MAX_DOCUMENT);
  const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  let raw;
  try {
    const opened = await handle.stat();
    need(opened.isFile() && opened.dev === before.dev && opened.ino === before.ino
      && opened.size === before.size && opened.nlink === 1);
    raw = await handle.readFile();
    const after = await handle.stat();
    need(raw.length === before.size && after.size === before.size
      && after.mtimeMs === before.mtimeMs && after.ctimeMs === before.ctimeMs);
  } finally { await handle.close(); }
  abort(signal);
  const document = JSON.parse(decoder.decode(raw));
  need(document.schema === 'video.reference-learning-input.v1');
  const snapshot = document.knowledge_snapshot;
  need(snapshot === null || (plain(snapshot) && Array.isArray(snapshot.pages) && snapshot.pages.length <= 256
    && snapshot.schema === 'video.animation-knowledge-snapshot.v1'));
  need((document.knowledge_binding?.enabled !== false && document.knowledge_binding?.use_wikiskill !== false)
    || snapshot === null);
  return { document, snapshot, input_sha256: createHash('sha256').update(raw).digest('hex') };
}

const catalogue = snapshot => (snapshot?.pages || []).map(page => ({
  page_id: page.page_id, revision: page.revision, sha256: page.sha256,
  title: page.body.title, applicability: page.body.applicability || [], status: page.status,
}));

export async function readReferenceInputs(params, signal) {
  need(permitted(params, []) && Object.keys(params).length === 0);
  const { document, snapshot, input_sha256 } = await frozenTask(signal);
  const read = await readInputs({}, signal);
  need(read.content.filter(item => item.type === 'text' && item.text.startsWith('Input file: ' + INPUT + '\n')).length === 1);
  const view = { ...document, knowledge_snapshot: snapshot === null ? null : {
    schema: 'video.reference-learning-knowledge-catalog.v1', snapshot_sha256: snapshot.sha256,
    pages: catalogue(snapshot), content_access: 'skill_search_and_skill_read',
  } };
  const content = read.content.map(item => item.type === 'text' && item.text.startsWith('Input file: ' + INPUT + '\n')
    ? { type: 'text', text: `Input file: ${INPUT}\n${JSON.stringify(view)}` } : item);
  return { content, details: { ...read.details, source: 'fixed-reference-learning-inputs',
    input_sha256, knowledge_body_visibility: 'on_demand_only', snapshot_sha256: snapshot?.sha256 || null } };
}

export async function searchReferenceSkills(params, signal) {
  need(permitted(params, ['query', 'limit']) && typeof (params.query ?? '') === 'string'
    && (params.query ?? '').length <= 1000);
  const limit = params.limit ?? 8;
  need(Number.isInteger(limit) && limit >= 1 && limit <= 20);
  const { snapshot, input_sha256 } = await frozenTask(signal);
  const query = (params.query ?? '').trim().toLocaleLowerCase();
  const rows = catalogue(snapshot).filter(row => !query
    || [row.page_id, row.title, ...row.applicability].join('\n').toLocaleLowerCase().includes(query));
  const result = { snapshot_sha256: snapshot?.sha256 || null, pages: rows.slice(0, limit),
    total: rows.length, truncated: rows.length > limit };
  return { content: [{ type: 'text', text: JSON.stringify(result) }], details: {
    source: 'frozen-reference-knowledge-catalogue', input_sha256, snapshot_sha256: snapshot?.sha256 || null } };
}

export async function readReferenceSkill(params, signal) {
  need(permitted(params, ['page_id', 'offset', 'limit']) && typeof params.page_id === 'string'
    && /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,199}$/.test(params.page_id));
  const offset = params.offset ?? 0, limit = params.limit ?? 8000;
  need(Number.isInteger(offset) && offset >= 0 && offset <= 64000
    && Number.isInteger(limit) && limit >= 1 && limit <= 16000);
  const { snapshot, input_sha256 } = await frozenTask(signal);
  const page = snapshot?.pages.find(row => row.page_id === params.page_id);
  need(page && plain(page.body) && typeof page.body.content === 'string' && offset <= page.body.content.length);
  const text = page.body.content.slice(offset, offset + limit);
  const result = { page_id: page.page_id, revision: page.revision, sha256: page.sha256,
    title: page.body.title, text, offset, total_characters: page.body.content.length,
    next_offset: offset + text.length < page.body.content.length ? offset + text.length : null,
    applicability: page.body.applicability || [], limitations: page.body.limitations || [] };
  return { content: [{ type: 'text', text: JSON.stringify(result) }], details: {
    source: 'frozen-reference-knowledge-page', input_sha256, snapshot_sha256: snapshot.sha256,
    page_sha256: page.sha256, page_id: page.page_id, offset, characters: text.length,
    understanding_verified: false, application_verified: false } };
}

export default function (pi) {
  for (const [name, description, properties, required, execute] of [
    ['read_inputs', 'Read frozen task, original images and catalogue. Knowledge page bodies remain hidden until requested.', {}, [], readReferenceInputs],
    ['skill_search', 'Search the exact frozen knowledge catalogue. Empty query lists pages. No external or mutable knowledge.',
      { query: { type: 'string', maxLength: 1000 }, limit: { type: 'integer', minimum: 1, maximum: 20 } }, [], searchReferenceSkills],
    ['skill_read', 'Read a bounded part of one exact frozen knowledge page. Disabled or unknown pages fail.',
      { page_id: { type: 'string', maxLength: 200 }, offset: { type: 'integer', minimum: 0, maximum: 64000 },
        limit: { type: 'integer', minimum: 1, maximum: 16000 } }, ['page_id'], readReferenceSkill],
  ]) pi.registerTool({ name, label: name, description,
    parameters: { type: 'object', properties, required, additionalProperties: false },
    execute: async (_id, params, signal) => execute(params, signal) });
}
