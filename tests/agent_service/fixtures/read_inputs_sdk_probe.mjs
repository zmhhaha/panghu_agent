// Real pinned Pi agentLoop, local stub model only. No HTTP/client credentials.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { pathToFileURL } from 'node:url';

const need = (condition, reason) => { if (!condition) throw new Error(reason); };
const root = '/usr/local/lib/node_modules/@earendil-works/pi-coding-agent';
const core = root + '/node_modules/@earendil-works/pi-agent-core';
const codingPackage = JSON.parse(fs.readFileSync(root + '/package.json', 'utf8'));
const corePackage = JSON.parse(fs.readFileSync(core + '/package.json', 'utf8'));
need(codingPackage.version === '0.99.2', 'pinned_sdk_required');
const { agentLoop } = await import(pathToFileURL(core + '/dist/agent-loop.js').href);
const extensionPath = process.argv[2] || '/probe/read_inputs_extension.mjs';
const { default: register } = await import(pathToFileURL(extensionPath).href);
let tool;
register({ registerTool(value) { need(!tool, 'only_one_extension_tool'); tool = value; } });
need(tool.name === 'read_inputs' && !('path' in tool.parameters.properties), 'noargument_reader_required');
const fixture = fs.mkdtempSync('/tmp/read-inputs-sdk-');
const before = process.cwd();
try {
  process.chdir(fixture);
  fs.mkdirSync(path.join(fixture, 'inputs'));
  fs.writeFileSync(path.join(fixture, 'inputs/task.json'), JSON.stringify({ fixture: true, frame_count: 24 }));
  const png = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAIAAACQd1PeAAAADElEQVR4nGP4z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC', 'base64');
  const originals = [];
  for (let index = 0; index < 24; index++) {
    const raw = Buffer.concat([png, Buffer.from([index])]);
    originals.push(raw);
    fs.writeFileSync(path.join(fixture, 'inputs/frame-' + String(index).padStart(2, '0') + '.png'), raw);
  }
  let modelCalls = 0, toolStarts = 0, toolEnds = 0;
  const usage = { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0,
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } };
  const streamFn = async (_model, context) => {
    modelCalls++;
    need(modelCalls <= 2, 'exact_two_stub_model_turns');
    const results = context.messages.filter(message => message.role === 'toolResult');
    if (modelCalls === 2) {
      need(results.length === 1 && results[0].isError !== true, 'single_successful_tool_result');
      const images = results[0].content.filter(part => part.type === 'image');
      need(images.length === 24, 'all_24_images_in_second_context');
      need(images.every((image, index) => image.mimeType === 'image/png'
        && Buffer.from(image.data, 'base64').equals(originals[index])), 'all_original_image_bytes_preserved');
    } else {
      need(results.length === 0, 'fresh_context_required');
    }
    const message = { role: 'assistant',
      content: modelCalls === 1 ? [{ type: 'toolCall', id: 'fixture-read-inputs', name: 'read_inputs', arguments: {} }]
        : [{ type: 'text', text: 'offline complete' }],
      api: 'openai-responses', provider: 'offline-fixture', model: 'offline-fixture', usage,
      stopReason: modelCalls === 1 ? 'toolUse' : 'stop', timestamp: Date.now() };
    return { [Symbol.asyncIterator]: async function* () { yield { type: 'done', reason: message.stopReason, message }; },
      result: async () => message };
  };
  const model = { id: 'offline-fixture', name: 'offline-fixture', api: 'openai-responses', provider: 'offline-fixture',
    baseUrl: 'http://invalid.invalid', reasoning: false, input: ['text', 'image'],
    cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 128000, maxTokens: 1024 };
  const stream = agentLoop([{ role: 'user', content: [{ type: 'text', text: 'offline fixture' }], timestamp: Date.now() }],
    { systemPrompt: 'Offline only.', messages: [], tools: [tool] },
    { model, convertToLlm: messages => messages }, AbortSignal.timeout(15000), streamFn);
  for await (const event of stream) {
    if (event.type === 'tool_execution_start') toolStarts++;
    if (event.type === 'tool_execution_end') toolEnds++;
  }
  await stream.result();
  need(modelCalls === 2 && toolStarts === 1 && toolEnds === 1, 'one_batch_read_only');
  console.log(JSON.stringify({ ok: true, coding_version: codingPackage.version, core_version: corePackage.version,
    extension_sha256: crypto.createHash('sha256').update(fs.readFileSync(extensionPath)).digest('hex'),
    stub_model_turns: modelCalls, input_tool_calls: toolEnds, image_parts_in_second_context: 24,
    original_image_bytes_preserved: true, real_model_requests: 0, production_jobs: 0 }));
} finally {
  process.chdir(before);
  fs.rmSync(fixture, { recursive: true, force: true });
}
