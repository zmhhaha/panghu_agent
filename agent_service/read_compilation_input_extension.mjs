// Read-only, bounded access to the exact frozen WikiSkill input file set.
import { constants } from 'node:fs';
import { lstat, open, realpath } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';

const MANIFEST = 'animation-wikiskill-input.json';
const MAX_MANIFEST = 1024 * 1024;
const MAX_RECORD = 4 * 1024 * 1024;
const MAX_TOTAL = 32 * 1024 * 1024;
const MAX_REPLY = 32 * 1024;
const decoder = new TextDecoder('utf-8', { fatal: true });
const need = condition => { if (!condition) throw new Error('compilation_input_invalid'); };
const abort = signal => { if (signal?.aborted) throw new Error('compilation_input_cancelled'); };
const sha = raw => createHash('sha256').update(raw).digest('hex');
const checksum = value => typeof value === 'string' && /^[0-9a-f]{64}$/.test(value);
const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const keys = (value, names) => object(value) && Object.keys(value).sort().join(',') === [...names].sort().join(',');
const same = (before, after) => before.dev === after.dev && before.ino === after.ino
  && before.size === after.size && before.mtimeMs === after.mtimeMs && before.ctimeMs === after.ctimeMs;
const text = (value, maximum, empty = false) => typeof value === 'string'
  && [...value].length <= maximum && (empty || value.trim().length > 0)
  && !/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\ud800-\udfff]/u.test(value);

async function directory(file) {
  const info = await lstat(file);
  need(info.isDirectory() && !info.isSymbolicLink() && await realpath(file) === file);
  return info;
}

async function readFile(root, name, maximum, signal) {
  abort(signal);
  const parent = path.dirname(path.join(root, name));
  const beforeRoot = await directory(root);
  const beforeParent = parent === root ? beforeRoot : await directory(parent);
  const file = path.join(root, name);
  const before = await lstat(file);
  need(before.isFile() && !before.isSymbolicLink() && before.nlink === 1
    && before.size > 0 && before.size <= maximum);
  const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  let raw;
  try {
    const opened = await handle.stat();
    need(opened.isFile() && opened.nlink === 1 && same(before, opened));
    const buffer = Buffer.alloc(before.size + 1);
    let used = 0;
    while (used < buffer.length) {
      abort(signal);
      const { bytesRead } = await handle.read(buffer, used, buffer.length - used, used);
      if (bytesRead === 0) break;
      used += bytesRead;
    }
    raw = buffer.subarray(0, used);
    need(raw.length === before.size && same(before, await handle.stat()));
  } finally {
    await handle.close();
  }
  need(same(before, await lstat(file)) && same(beforeRoot, await directory(root))
    && same(beforeParent, await directory(parent)));
  abort(signal);
  return raw;
}

function manifest(raw) {
  const value = JSON.parse(decoder.decode(raw));
  need(keys(value, ['schema', 'snapshot', 'records', 'note', 'evidence_scope'])
    && value.schema === 'video.animation-wikiskill-input.v1'
    && value.evidence_scope === 'frozen_records_only_no_new_media_validation'
    && text(value.note, 4000, true)
    && keys(value.snapshot, ['schema', 'id', 'owner', 'captured_at', 'sha256'])
    && value.snapshot.schema === 'video.animation-wikiskill-snapshot.v1'
    && typeof value.snapshot.id === 'string'
    && /^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$/.test(value.snapshot.id)
    && text(value.snapshot.owner, 400) && checksum(value.snapshot.sha256)
    && Number.isFinite(value.snapshot.captured_at) && value.snapshot.captured_at > 0
    && Array.isArray(value.records) && value.records.length > 0 && value.records.length <= 2047);
  let total = raw.length;
  const ids = new Set();
  for (const [index, row] of value.records.entries()) {
    need(keys(row, ['record_id', 'kind', 'sha256', 'file_name', 'file_sha256', 'bytes'])
      && text(row.record_id, 400) && !/[\r\n\t]/.test(row.record_id)
      && text(row.kind, 400) && !/[\r\n\t]/.test(row.kind)
      && checksum(row.sha256) && checksum(row.file_sha256)
      && row.file_name === `records/${String(index).padStart(6, '0')}.json`
      && Number.isSafeInteger(row.bytes) && row.bytes > 0 && row.bytes <= MAX_RECORD
      && !ids.has(row.record_id));
    ids.add(row.record_id);
    total += row.bytes;
  }
  need(total <= MAX_TOTAL);
  return value;
}

function result(value, inputSha256) {
  const encoded = JSON.stringify(value);
  need(Buffer.byteLength(encoded) <= MAX_REPLY);
  return { content: [{ type: 'text', text: encoded }],
    details: { source: 'fixed-read-only-compilation-input', inputSha256,
      replyBytes: Buffer.byteLength(encoded), maximumReplyBytes: MAX_REPLY } };
}

async function recordRange(root, value, name, offset, maximum, signal) {
  need(typeof name === 'string' && /^records\/[0-9]{6}\.json$/.test(name)
    && Number.isSafeInteger(offset) && offset >= 0
    && Number.isSafeInteger(maximum) && maximum >= 4 && maximum <= 16384);
  const row = value.records.find(item => item.file_name === name);
  need(row && offset <= row.bytes);
  const raw = await readFile(root, row.file_name, MAX_RECORD, signal);
  need(raw.length === row.bytes && sha(raw) === row.file_sha256);
  JSON.parse(decoder.decode(raw));
  need(offset === raw.length || (raw[offset] & 0xc0) !== 0x80);
  let end = Math.min(raw.length, offset + maximum);
  while (end < raw.length && (raw[end] & 0xc0) === 0x80) end--;
  return { raw, row, offset, end };
}

function fragment(range) {
  return { file_name: range.row.file_name, file_sha256: range.row.file_sha256,
    bytes: range.row.bytes, offset_bytes: range.offset,
    next_offset_bytes: range.end === range.raw.length ? null : range.end,
    text: decoder.decode(range.raw.subarray(range.offset, range.end)) };
}

function shorten(range) {
  range.end = range.offset + Math.floor((range.end - range.offset) / 2);
  while (range.end < range.raw.length && (range.raw[range.end] & 0xc0) === 0x80) range.end--;
  need(range.end > range.offset);
}

export async function readCompilationInput(params, signal) {
  need(object(params) && checksum(params.input_sha256));
  abort(signal);
  const root = path.resolve(process.cwd(), 'inputs');
  const raw = await readFile(root, MANIFEST, MAX_MANIFEST, signal);
  need(sha(raw) === params.input_sha256);
  const value = manifest(raw);
  if (params.mode === 'manifest') {
    need(keys(params, ['mode', 'input_sha256', 'offset', 'limit'])
      && Number.isSafeInteger(params.offset) && params.offset >= 0 && params.offset <= value.records.length
      && Number.isSafeInteger(params.limit) && params.limit >= 1 && params.limit <= 128);
    const output = { mode: 'manifest', input_sha256: params.input_sha256, input_bytes: raw.length,
      schema: value.schema, snapshot: value.snapshot, note: value.note, evidence_scope: value.evidence_scope,
      records: [], offset: params.offset, total_records: value.records.length, next_offset: params.offset };
    for (const row of value.records.slice(params.offset, params.offset + params.limit)) {
      output.records.push(row);
      output.next_offset++;
      if (Buffer.byteLength(JSON.stringify(output)) > MAX_REPLY) {
        output.records.pop(); output.next_offset--; break;
      }
    }
    need(output.records.length > 0 || params.offset === value.records.length);
    if (output.next_offset === value.records.length) output.next_offset = null;
    return result(output, params.input_sha256);
  }
  if (params.mode === 'batch') {
    need(keys(params, ['mode', 'input_sha256', 'file_names'])
      && Array.isArray(params.file_names) && params.file_names.length >= 1 && params.file_names.length <= 32
      && new Set(params.file_names).size === params.file_names.length);
    const output = { mode: 'batch', input_sha256: params.input_sha256, files: [], next_file_index: 0 };
    for (const name of params.file_names) {
      const range = await recordRange(root, value, name, 0, 8192, signal);
      output.files.push(fragment(range));
      output.next_file_index++;
      if (Buffer.byteLength(JSON.stringify(output)) > MAX_REPLY) {
        if (output.files.length > 1) {
          output.files.pop(); output.next_file_index--; break;
        }
        while (Buffer.byteLength(JSON.stringify(output)) > MAX_REPLY) {
          shorten(range); output.files[0] = fragment(range);
        }
      }
    }
    if (output.next_file_index === params.file_names.length) output.next_file_index = null;
    abort(signal);
    return result(output, params.input_sha256);
  }
  need(params.mode === 'file' && keys(params, ['mode', 'input_sha256', 'file_name', 'offset_bytes', 'max_bytes']));
  const range = await recordRange(root, value, params.file_name, params.offset_bytes, params.max_bytes, signal);
  const output = { mode: 'file', input_sha256: params.input_sha256, ...fragment(range) };
  while (true) {
    if (Buffer.byteLength(JSON.stringify(output)) <= MAX_REPLY) break;
    shorten(range); Object.assign(output, fragment(range));
  }
  abort(signal);
  return result(output, params.input_sha256);
}

export default function (pi) {
  pi.registerTool({ name: 'read_compilation_input', label: 'Read frozen compilation evidence',
    description: 'Read the SHA-bound frozen manifest in bounded record-index pages, or original manifest-listed JSON records in bounded UTF-8 byte ranges. Start with mode=manifest, offset=0, limit=128; follow next_offset. Use mode=batch, file_names=[up to 32 exact record paths] to read initial ranges efficiently; next_file_index identifies unreturned names. For a record continuation use mode=file, exact file_name, offset_bytes=the prior next_offset_bytes, max_bytes=16384. Every call requires task input_sha256. No arbitrary paths, URLs, shell, writes or execution.',
    parameters: { type: 'object', properties: {
      mode: { type: 'string', enum: ['manifest', 'file', 'batch'] },
      input_sha256: { type: 'string', pattern: '^[0-9a-f]{64}$' },
      offset: { type: 'integer', minimum: 0 }, limit: { type: 'integer', minimum: 1, maximum: 128 },
      file_name: { type: 'string', pattern: '^records/[0-9]{6}\\.json$' },
      file_names: { type: 'array', minItems: 1, maxItems: 32, uniqueItems: true,
        items: { type: 'string', pattern: '^records/[0-9]{6}\\.json$' } },
      offset_bytes: { type: 'integer', minimum: 0 }, max_bytes: { type: 'integer', minimum: 4, maximum: 16384 },
    }, required: ['mode', 'input_sha256'], additionalProperties: false },
    execute: async (_id, params, signal) => readCompilationInput(params, signal),
  });
}
