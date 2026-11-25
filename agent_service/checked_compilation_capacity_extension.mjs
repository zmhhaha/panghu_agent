// V4 revision 2 selects its separately pinned capacity reader; the original reader remains frozen.
import { constants } from 'node:fs';
import { lstat, open, realpath } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import path from 'node:path';
import registerReader from './read_compilation_capacity_extension.mjs';
import { inspectCompilationOutput } from './compilation_output_preflight.mjs';

const decoder = new TextDecoder('utf-8', { fatal: true });
const sha = raw => createHash('sha256').update(raw).digest('hex');
const same = (a, b) => a.dev === b.dev && a.ino === b.ino && a.size === b.size
  && a.mtimeMs === b.mtimeMs && a.ctimeMs === b.ctimeMs;
const abort = signal => { if (signal?.aborted) throw new Error('compilation_check_cancelled'); };
const need = condition => { if (!condition) throw new Error('compilation_check_file_invalid'); };

async function directory(file) {
  const info = await lstat(file);
  need(info.isDirectory() && !info.isSymbolicLink() && await realpath(file) === file);
  return info;
}

async function fixedFile(root, name, maximum, signal) {
  abort(signal);
  const beforeRoot = await directory(root), target = path.join(root, name);
  const info = await lstat(target);
  need(info.isFile() && !info.isSymbolicLink() && info.nlink === 1 && info.size > 0 && info.size <= maximum);
  const handle = await open(target, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  let raw;
  try {
    need(same(info, await handle.stat()));
    const buffer = Buffer.alloc(info.size + 1); let used = 0;
    while (used < buffer.length) {
      abort(signal);
      const { bytesRead } = await handle.read(buffer, used, buffer.length - used, used);
      if (bytesRead === 0) break;
      used += bytesRead;
    }
    raw = buffer.subarray(0, used);
    need(raw.length === info.size && same(info, await handle.stat()));
  } finally { await handle.close(); }
  need(same(info, await lstat(target)) && same(beforeRoot, await directory(root)));
  abort(signal); return raw;
}

export async function checkCompilationOutput(params, signal) {
  if (!params || Object.keys(params).length !== 1 || typeof params.input_sha256 !== 'string'
      || !/^[0-9a-f]{64}$/.test(params.input_sha256)) throw new Error('compilation_check_parameters_invalid');
  const root = path.resolve(process.cwd());
  const input = await fixedFile(path.join(root, 'inputs'), 'animation-wikiskill-input.json', 1048576, signal);
  if (sha(input) !== params.input_sha256) throw new Error('compilation_check_input_binding_mismatch');
  const document = JSON.parse(decoder.decode(input));
  if (document.schema !== 'video.animation-wikiskill-input.v1' || !Array.isArray(document.records)
      || !document.snapshot) throw new Error('compilation_check_input_binding_mismatch');
  let raw, value, error;
  try { raw = await fixedFile(root, 'output.json', 16777216, signal); }
  catch (cause) {
    abort(signal); error = cause?.code === 'ENOENT' ? 'output_json_missing' : 'output_json_unavailable_unsafe_or_size_limit';
  }
  if (!error) {
    try { value = JSON.parse(decoder.decode(raw)); }
    catch { error = 'output_json_invalid_utf8_or_json'; }
  }
  const report = error ? { valid: false, errors: [{ path: '$', code: error }], errors_truncated: false }
    : inspectCompilationOutput(value, document, params.input_sha256);
  abort(signal);
  const output = { ...report, input_sha256: params.input_sha256,
    checked_output_sha256: raw ? sha(raw) : null, read_only: true,
    scope: 'Structural preflight of Agent-authored output only; final stopped Python validation remains required. No visual, audio, factual or publication approval.' };
  return { content: [{ type: 'text', text: JSON.stringify(output) }],
    details: { source: 'fixed-read-only-compilation-output-check', readOnly: true } };
}

export default function (pi) {
  registerReader(pi);
  pi.registerTool({ name: 'check_compilation_output', label: 'Check authored WikiSkill output',
    description: 'After writing output.json, check its exact compact JSON fields, source index ranges, complete coverage, hierarchy and Skill package structure. Read-only fixed paths; returns concrete JSON-path errors. Fix the file yourself with write, then check again until valid. Does not repair prose, run programs, access network, render or publish. Python performs final complete validation after all writers stop.',
    parameters: { type: 'object', properties: { input_sha256: { type: 'string', pattern: '^[0-9a-f]{64}$' } },
      required: ['input_sha256'], additionalProperties: false },
    execute: async (_id, params, signal) => checkCompilationOutput(params, signal),
  });
}
