// Bounded pi-cage print-mode transport. This file never changes model/tool data
// inside Pi: it only removes repeated snapshots from the diagnostic stdout wire.
import { fileURLToPath } from 'node:url';

export const LIMITS = Object.freeze({
  rawBytes: 512 * 1024 * 1024,
  lineBytes: 4 * 1024 * 1024,
  outputBytes: 32 * 1024 * 1024,
  events: 100000,
});

export class CompactStream {
  constructor(write, limits = LIMITS) {
    this.write = write;
    this.limits = limits;
    this.pending = '';
    this.failed = false;
    this.stats = { raw_bytes: 0, raw_lines: 0, raw_max_line_bytes: 0,
      output_bytes: 0, output_lines: 0, output_max_line_bytes: 0,
      message_updates: 0, tool_updates: 0 };
  }

  output(value) {
    const text = JSON.stringify(value) + '\n';
    const bytes = Buffer.byteLength(text);
    if (bytes > this.limits.lineBytes || this.stats.output_bytes + bytes > this.limits.outputBytes) {
      this.fail('runtime_output_limit');
      return;
    }
    this.stats.output_bytes += bytes;
    this.stats.output_lines++;
    this.stats.output_max_line_bytes = Math.max(this.stats.output_max_line_bytes, bytes);
    this.write(text);
  }

  metrics() {
    return { type: 'runtime_output_stats', ...this.stats };
  }

  fail(code) {
    if (this.failed) return;
    this.failed = true;
    this.pending = '';
    // A small fixed diagnostic is outside the compactor's output budget. The
    // Python controller still bounds every physical stdout/stderr byte.
    this.write(JSON.stringify(this.metrics()) + '\n');
    this.write(JSON.stringify({ type: 'runtime_stream_error', code }) + '\n');
  }

  feed(chunk) {
    if (this.failed) return;
    const text = Buffer.isBuffer(chunk) ? chunk.toString('utf8') : chunk;
    if (typeof text !== 'string') return this.fail('runtime_protocol_error');
    if (Buffer.isBuffer(chunk) && !Buffer.from(text, 'utf8').equals(chunk)) return this.fail('runtime_protocol_error');
    this.stats.raw_bytes += Buffer.byteLength(text);
    if (this.stats.raw_bytes > this.limits.rawBytes) return this.fail('runtime_raw_output_limit');
    // print-mode writes one complete JSON line. Bound partial writes too, so
    // an unterminated or unexpectedly large line cannot grow retained memory.
    let start = 0;
    for (;;) {
      const end = text.indexOf('\n', start);
      const part = end < 0 ? text.slice(start) : text.slice(start, end);
      if (Buffer.byteLength(this.pending) + Buffer.byteLength(part) + 1 > this.limits.lineBytes) {
        return this.fail('runtime_output_line_limit');
      }
      this.pending += part;
      if (end < 0) return;
      const line = this.pending;
      this.pending = '';
      this.stats.raw_lines++;
      this.stats.raw_max_line_bytes = Math.max(this.stats.raw_max_line_bytes, Buffer.byteLength(line) + 1);
      if (this.stats.raw_lines > this.limits.events) return this.fail('runtime_event_limit');
      let event;
      try { event = JSON.parse(line); } catch { return this.fail('runtime_protocol_error'); }
      if (!event || Array.isArray(event) || typeof event.type !== 'string') return this.fail('runtime_protocol_error');
      if (event.type === 'message_update') {
        this.stats.message_updates++;
        const update = event.assistantMessageEvent;
        if (!update || Array.isArray(update) || typeof update.type !== 'string') return this.fail('runtime_protocol_error');
        // Pi repeats the same growing assistant in both fields. Preserve
        // each delta and its metadata exactly, never the two cumulative copies.
        const { partial: _partial, ...delta } = update;
        event = { type: event.type, assistantMessageEvent: delta };
      } else if (event.type === 'tool_execution_update') {
        this.stats.tool_updates++;
        // The terminal tool_execution_end retains the complete bounded result.
        event = { type: event.type };
      } else if (event.type === 'turn_end') {
        // message_end already carried the complete assistant. Keep precisely
        // the identity/error fields that the Python adapter verifies again.
        const message = event.message;
        event = { type: event.type };
        if (message && typeof message === 'object' && !Array.isArray(message)) {
          event.message = {};
          for (const key of ['role', 'provider', 'model', 'stopReason', 'errorMessage']) {
            if (Object.hasOwn(message, key)) event.message[key] = message[key];
          }
        }
      } else if (event.type === 'agent_end') {
        event = { type: event.type };
      }
      this.output(event);
      if (this.failed) return;
      if (this.stats.raw_lines % 256 === 0 || event.type === 'agent_end') this.output(this.metrics());
      if (this.failed) return;
      start = end + 1;
      if (start === text.length) return;
    }
  }

  finish() {
    if (this.pending) this.fail('runtime_protocol_error');
  }
}

// Only the trusted controller's explicit --import activates the writer. Ordinary
// test imports use the pure bounded parser above. No environment or job-selected
// loader path is accepted by the controller.
if (process.execArgv.includes(fileURLToPath(import.meta.url)) || process.execArgv.includes(import.meta.url)) {
  const nativeWrite = process.stdout.write.bind(process.stdout);
  let output = [];
  const compact = new CompactStream(text => output.push(text));
  process.stdout.write = function (chunk, encoding, callback) {
    if (typeof encoding === 'function') { callback = encoding; encoding = undefined; }
    // The pinned print mode writes UTF-8 strings. Reject an unreviewed encoding
    // rather than reinterpret arbitrary bytes or emit undecodable JSON.
    if (encoding && encoding !== 'utf8' && encoding !== 'utf-8') compact.fail('runtime_protocol_error');
    else compact.feed(chunk);
    const text = output.join('');
    output = [];
    if (compact.failed) {
      nativeWrite(text, 'utf8', () => process.exit(70));
      return false;
    }
    if (text) return nativeWrite(text, 'utf8', callback);
    if (callback) queueMicrotask(callback);
    return true;
  };
  process.on('beforeExit', () => {
    compact.finish();
    if (output.length) {
      const text = output.join(''); output = [];
      nativeWrite(text);
      process.exitCode = 70;
    }
  });
}
