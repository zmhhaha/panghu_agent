// Attach read-only source validation to the already admitted write tool.
import { execFile } from 'node:child_process';
import path from 'node:path';
import { promisify } from 'node:util';

const run = promisify(execFile);
const packageRoot = '/opt/agent-service';
const python = packageRoot + '/.venv/bin/python';

export default function (pi) {
  pi.on('tool_result', async (event, context) => {
    if (event.toolName !== 'write' || event.isError || typeof event.input?.path !== 'string') return;
    const workspace = context.cwd;
    if (path.resolve(workspace, event.input.path) !== path.join(workspace, 'output.json')) return;

    let check;
    try {
      const { stdout } = await run(python, ['-P', '-B', '-m', 'agent_service.learning_source_validation', workspace], {
        cwd: packageRoot, env: { PYTHONPATH: packageRoot }, timeout: 7500, maxBuffer: 4096,
      });
      check = JSON.parse(stdout);
    } catch {
      check = { source_syntax: 'check_failed', code: 'learning_source_syntax_check_unavailable' };
    }
    const content = [{ type: 'text', text: JSON.stringify(check) }];
    return { content, details: { learning_source_check: check }, isError: check.source_syntax !== 'checked' };
  });
}
