// Syntax-only checker. The input is produced by the image-owned Python collector.
import { spawnSync } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { Script, SourceTextModule } from 'node:vm';

const environment = process.env.SystemRoot ? { SystemRoot: process.env.SystemRoot } : {};
const programs = JSON.parse(readFileSync(0, 'utf8'));
for (const [index, { goal, text }] of programs.entries()) {
  try {
    if (goal === 'module') new SourceTextModule(text, { identifier: 'scene-source' });
    else new Script(text, { filename: 'scene-source' });
  } catch (error) {
    if (!(error instanceof SyntaxError)) throw error;
    let diagnostic = error.stack;
    let header = 'scene-source';
    if (goal === 'module') {
      // SourceTextModule does not expose line/column. Use the same V8 parser
      // in Node's syntax-only mode for the single failing module.
      diagnostic = spawnSync(process.execPath, ['--check', '--input-type=module'], {
        input: text, encoding: 'utf8', env: environment, maxBuffer: 512 * 1024, timeout: 1000, windowsHide: true,
      }).stderr;
      header = '[stdin]';
    }
    const lines = diagnostic?.split(/\r?\n/) || [];
    const location = lines.findIndex(line => line.startsWith(header + ':')
      && /^[1-9][0-9]*$/.test(line.slice(header.length + 1)));
    const carets = lines[location + 2];
    const column = location >= 0 && /^\s*\^+\s*$/.test(carets || '')
      ? carets.indexOf('^') + 1 : undefined;
    console.log(JSON.stringify({ index, line: location >= 0
      ? Number(lines[location].slice(header.length + 1)) : undefined, column }));
    process.exit(0);
  }
}
console.log('{}');
