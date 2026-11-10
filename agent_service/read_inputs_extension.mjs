// Fixed local input reader. No path, URL, shell, or file selection is accepted.
import { constants } from 'node:fs';
import { lstat, open, readdir, realpath } from 'node:fs/promises';
import path from 'node:path';

const MAX_FILES = 64;
const MAX_FILE_BYTES = 2 * 1024 * 1024;
const MAX_TOTAL_BYTES = 32 * 1024 * 1024;
const decoder = new TextDecoder('utf-8', { fatal: true });
const need = (condition) => { if (!condition) throw new Error('read_inputs_invalid'); };
const abort = (signal) => { if (signal?.aborted) throw new Error('read_inputs_cancelled'); };

export async function readInputs(params, signal) {
  need(params !== null && typeof params === 'object' && !Array.isArray(params)
    && Object.keys(params).length === 0);
  abort(signal);
  const root = path.resolve(process.cwd(), 'inputs');
  const directory = await lstat(root);
  need(directory.isDirectory() && !directory.isSymbolicLink() && await realpath(root) === root);
  const names = await readdir(root);
  need(names.length > 0 && names.length <= MAX_FILES && names.every(name =>
    /^[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\.(?:json|png|jpg|jpeg)$/.test(name)));
  // JSON describes input identity/order; each image is immediately preceded by
  // its exact filename. Original bytes are returned without resizing/conversion.
  names.sort((left, right) => Number(!left.endsWith('.json')) - Number(!right.endsWith('.json'))
    || left.localeCompare(right, 'en'));
  let total = 0;
  const content = [], files = [];
  for (const name of names) {
    abort(signal);
    const file = path.join(root, name);
    const before = await lstat(file);
    need(before.isFile() && !before.isSymbolicLink() && before.nlink === 1
      && before.size > 0 && before.size <= MAX_FILE_BYTES);
    total += before.size;
    need(total <= MAX_TOTAL_BYTES);
    const handle = await open(file, constants.O_RDONLY | (constants.O_NOFOLLOW || 0));
    let raw;
    try {
      const opened = await handle.stat();
      need(opened.isFile() && opened.dev === before.dev && opened.ino === before.ino
        && opened.size === before.size && opened.nlink === 1);
      const buffer = Buffer.alloc(before.size + 1);
      let used = 0;
      while (used < buffer.length) {
        abort(signal);
        const { bytesRead } = await handle.read(buffer, used, buffer.length - used, used);
        if (bytesRead === 0) break;
        used += bytesRead;
      }
      raw = buffer.subarray(0, used);
      const after = await handle.stat();
      need(raw.length === before.size && after.size === before.size
        && after.mtimeMs === before.mtimeMs && after.ctimeMs === before.ctimeMs);
    } finally {
      await handle.close();
    }
    if (name.endsWith('.json')) {
      const text = decoder.decode(raw);
      JSON.parse(text);
      content.push({ type: 'text', text: `Input file: ${name}\n${text}` });
      files.push({ name, bytes: raw.length, mediaType: 'application/json' });
    } else {
      const png = raw.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]));
      const jpeg = raw[0] === 255 && raw[1] === 216 && raw[2] === 255;
      const mimeType = name.endsWith('.png') ? 'image/png' : 'image/jpeg';
      need(mimeType === 'image/png' ? png : jpeg);
      content.push({ type: 'text', text: `Input file: ${name}` });
      content.push({ type: 'image', data: raw.toString('base64'), mimeType });
      files.push({ name, bytes: raw.length, mediaType: mimeType });
    }
  }
  abort(signal);
  return { content, details: { source: 'fixed-read-only-inputs', files, totalBytes: total } };
}

export default function (pi) {
  pi.registerTool({
    name: 'read_inputs', label: 'Read all task inputs',
    description: 'Read every host-staged JSON and PNG/JPEG file in the fixed read-only inputs directory in one call. Returns exact text and original image bytes with filenames. No arguments or paths are accepted.',
    parameters: { type: 'object', properties: {}, additionalProperties: false },
    execute: async (_id, params, signal) => readInputs(params, signal),
  });
}
