// Separately admitted task reader for progressively loaded Skill packages.
// The legacy read_inputs extension and its all-files behavior stay unchanged.
import { constants } from 'node:fs';
import { lstat, open, readdir, realpath } from 'node:fs/promises';
import path from 'node:path';

const need = value => { if (!value) throw new Error('skill_task_input_invalid'); };
const abort = signal => { if (signal?.aborted) throw new Error('skill_task_input_cancelled'); };
const decoder = new TextDecoder('utf-8', { fatal: true });
const MAX_BYTES = 2 * 1024 * 1024;

export async function readInputs(params, signal) {
  need(params !== null && typeof params === 'object' && !Array.isArray(params)
    && Object.keys(params).length === 0);
  abort(signal);
  const root = path.resolve(process.cwd(), 'inputs');
  const info = await lstat(root);
  need(info.isDirectory() && !info.isSymbolicLink() && await realpath(root) === root);
  const names = await readdir(root);
  need(names.length === 2 && names.includes('animation-skills'));
  const name = names.find(value => value !== 'animation-skills');
  need(/^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.json$/.test(name));
  const directory = path.join(root, 'animation-skills');
  const skillInfo = await lstat(directory);
  need(skillInfo.isDirectory() && !skillInfo.isSymbolicLink() && await realpath(directory) === directory);
  const file = path.join(root, name), before = await lstat(file);
  need(before.isFile() && !before.isSymbolicLink() && before.nlink === 1
    && before.size > 0 && before.size <= MAX_BYTES);
  const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
  let raw;
  try {
    const opened = await handle.stat();
    need(opened.isFile() && opened.dev === before.dev && opened.ino === before.ino
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
    need(raw.length === before.size && after.size === before.size
      && after.mtimeMs === before.mtimeMs && after.ctimeMs === before.ctimeMs);
  } finally { await handle.close(); }
  const text = decoder.decode(raw);
  JSON.parse(text);
  abort(signal);
  return { content: [{ type: 'text', text: `Input file: ${name}\n${text}` }],
    details: { source: 'fixed-read-only-skill-task', files: [{ name, bytes: raw.length,
      mediaType: 'application/json' }], totalBytes: raw.length,
      skill_bodies: 'use_skill_search_and_skill_read' } };
}

export default function (pi) {
  pi.registerTool({ name: 'read_inputs', label: 'Read the animation task',
    description: 'Read the single host-staged task JSON. Skill bodies remain in a separate protected directory: discover and read them with skill_search and skill_read. No arguments or paths are accepted.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    execute: async (_id, params, signal) => readInputs(params, signal) });
}
