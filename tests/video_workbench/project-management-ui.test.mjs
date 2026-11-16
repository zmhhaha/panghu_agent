import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createProjectManagement } from '../../video_workbench/web/project-management.mjs';

const html = await readFile(new URL('../../video_workbench/web/index.html', import.meta.url), 'utf8');
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.children = []; this.listeners = new Map(); this.dataset = {}; }
  set textContent(text) { this.text = String(text); this.children = []; }
  get textContent() { return [this.text ?? '', ...this.children.map(child => child.textContent)].join(''); }
  append(...items) { this.children.push(...items.flatMap(item => item.tagName === '#fragment' ? item.children : [item])); }
  replaceChildren(...items) { this.children = []; this.append(...items); }
  setAttribute(name, value) { this[name] = value; }
  addEventListener(name, callback) { this.listeners.set(name, callback); }
  async emit(name) { return this.listeners.get(name)?.({ preventDefault() {} }); }
  showModal() { this.open = true; }
  close() { this.open = false; }
}

function fixture() {
  const nodes = new Map([...html.matchAll(/<([\w-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)]
    .map(match => [match[2], new Element(match[1])]));
  const $ = id => { assert.ok(nodes.has(id), `Missing real element ${id}`); return nodes.get(id); };
  const document = { getElementById: $, createElement: tag => new Element(tag), createDocumentFragment: () => new Element('#fragment') };
  const p = { id: 'one', title: '<img src=x onerror=alert(1)>', revision: 4, scene_count: 3 };
  const context = { project: p, epoch: 1, unsaved: false, busy: false };
  const calls = [], removed = [], restored = [], errors = [], messages = [], pending = [];
  let active = [p], deleted = [], mode = 'ok', release;
  const request = async (path, options = {}) => {
    calls.push({ path, ...options });
    if (options.method === 'POST') {
      if (mode === 'delay') await new Promise(resolve => { release = resolve; });
      if (mode === '409') throw { status: 409, code: 'project_delete_busy' };
      if (mode !== 'unknown-unapplied') {
        if (path.endsWith('/trash')) { active = []; deleted = [{ ...p, revision: 5, deleted_at: 123 }]; }
        else { deleted = []; active = [{ ...p, revision: 6 }]; }
      }
      if (mode.startsWith('unknown')) throw new TypeError('response lost');
      return { project: path.endsWith('/trash') ? deleted[0] : active[0] };
    }
    return { projects: path.includes('?deleted=true') ? deleted : active };
  };
  let panel;
  panel = createProjectManagement({ document, request, getContext: () => context,
    onChange: () => panel.render(), onPending: (id, value) => pending.push([id, value]),
    onRemoved: async (id, epoch) => { removed.push({ id, epoch }); if (context.project?.id === id) { context.project = null; context.epoch++; } },
    onRestored: async (id, epoch) => restored.push({ id, epoch }),
    onError: error => errors.push(error), onSuccess: message => messages.push(message),
  });
  panel.render();
  return { $, panel, calls, removed, restored, errors, messages, pending, context,
    setMode(value) { mode = value; }, release() { release(); },
    setLists(nextActive, nextDeleted) { active = nextActive; deleted = nextDeleted; } };
}

test('delete requires confirmation, binds revision, and trash restores without regenerating media', async () => {
  const f = fixture();
  await f.$('delete-project').emit('click');
  assert.equal(f.calls.length, 0);
  assert.equal(f.$('delete-project-dialog').open, true);
  assert.equal(f.$('delete-project-name').textContent, '<img src=x onerror=alert(1)>');
  assert.equal(f.$('delete-project-name').children.length, 0, 'Project titles remain plain text');
  await f.$('cancel-delete-project').emit('click'); assert.equal(f.calls.length, 0);
  await f.$('delete-project').emit('click'); await f.$('confirm-delete-project').emit('click');
  assert.deepEqual(f.calls, [{ path: '/api/projects/one/trash', method: 'POST', body: { expected_revision: 4 } }]);
  assert.deepEqual(f.removed, [{ id: 'one', epoch: 1 }]);
  assert.equal(f.$('delete-project-dialog').open, false);
  await f.$('show-project-trash').emit('click');
  assert.equal(f.$('project-count').textContent, '1');
  const restore = f.$('project-list').children[0].children[1];
  await restore.emit('click');
  assert.deepEqual(f.calls.at(-1), { path: '/api/projects/one/restore', method: 'POST', body: { expected_revision: 5 } });
  assert.deepEqual(f.restored, [{ id: 'one', epoch: 2 }]);
  assert.equal(f.$('show-active-projects')['aria-pressed'], 'true');
  assert.equal(f.messages.length, 2);
});

test('busy and unsaved projects cannot delete, and cancellation never submits', async () => {
  const f = fixture();
  for (const field of ['busy', 'unsaved']) {
    f.context[field] = true; f.panel.render();
    assert.equal(f.$('delete-project').disabled, true);
    await f.$('delete-project').emit('click'); assert.equal(f.calls.length, 0);
    f.context[field] = false;
  }
  await f.$('delete-project').emit('click');
  f.context.unsaved = true;
  await f.$('confirm-delete-project').emit('click'); assert.equal(f.calls.length, 0);
  assert.match(f.errors.at(-1).message, /保存或撤销/);
});

test('lost committed write reconciles by GETs once, with no second POST or duplicate callback', async () => {
  const f = fixture(); f.setMode('unknown-applied');
  await f.$('delete-project').emit('click'); await f.$('confirm-delete-project').emit('click');
  assert.deepEqual(f.calls.map(call => [call.method ?? 'GET', call.path]), [
    ['POST', '/api/projects/one/trash'], ['GET', '/api/projects'], ['GET', '/api/projects?deleted=true'],
  ]);
  assert.equal(f.removed.length, 1);
  assert.equal(f.errors.length, 0);
  assert.equal(f.panel.hasUnresolvedOperation(), false);
  assert.deepEqual(f.pending.at(-1), ['one', false]);
});

test('unknown outcome keeps a visible read-only recovery action and blocks duplicate writes', async () => {
  const f = fixture(); f.setMode('unknown-unapplied');
  await f.$('delete-project').emit('click'); await f.$('confirm-delete-project').emit('click');
  assert.equal(f.panel.hasUnresolvedOperation(), true);
  assert.equal(f.$('reload-project-management').hidden, false);
  assert.equal(f.$('confirm-delete-project').disabled, true);
  assert.deepEqual(f.pending.at(-1), ['one', true]);
  await f.$('confirm-delete-project').emit('click');
  await f.$('reload-project-management').emit('click');
  assert.equal(f.calls.filter(call => call.method === 'POST').length, 1);
  f.setLists([], [{ id: 'one', revision: 5 }]);
  await f.$('reload-project-management').emit('click');
  assert.equal(f.removed.length, 1);
  assert.equal(f.panel.hasUnresolvedOperation(), false);
  assert.equal(f.$('reload-project-management').hidden, true);
});

test('project switch invalidates a confirmation and in-flight success stays bound to its original identity', async () => {
  const f = fixture();
  await f.$('delete-project').emit('click'); f.context.epoch++;
  f.context.project = { id: 'two', title: 'Other project', revision: 1 };
  f.panel.render();
  assert.equal(f.$('delete-project-dialog').open, false);
  await f.$('confirm-delete-project').emit('click'); assert.equal(f.calls.length, 0);
  f.setMode('delay'); await f.$('delete-project').emit('click');
  const result = f.$('confirm-delete-project').emit('click');
  await f.$('confirm-delete-project').emit('click');
  f.context.project = { id: 'three', title: 'New selection', revision: 1 }; f.context.epoch++;
  f.release(); await result;
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].path, '/api/projects/two/trash');
  assert.deepEqual(f.removed, [{ id: 'two', epoch: 2 }]);
  assert.equal(f.context.project.id, 'three');
});

test('server rejection closes stale confirmation and never cancels production work', async () => {
  const f = fixture(); f.setMode('409');
  await f.$('delete-project').emit('click'); await f.$('confirm-delete-project').emit('click');
  assert.equal(f.errors[0].code, 'project_delete_busy');
  assert.equal(f.removed.length, 0);
  assert.equal(f.$('delete-project-dialog').open, false);
  assert.equal(f.calls.length, 1);
  assert.equal(f.calls[0].path, '/api/projects/one/trash');
});

test('a lost restore response opens the restored identity once after read-only confirmation', async () => {
  const f = fixture();
  f.setLists([], [{ id: 'one', title: 'Restorable', revision: 5, scene_count: 3, deleted_at: 123 }]);
  await f.$('show-project-trash').emit('click'); f.setMode('unknown-applied');
  await f.$('project-list').children[0].children[1].emit('click');
  assert.deepEqual(f.calls.map(call => [call.method ?? 'GET', call.path]), [
    ['GET', '/api/projects?deleted=true'], ['POST', '/api/projects/one/restore'],
    ['GET', '/api/projects'], ['GET', '/api/projects?deleted=true'],
  ]);
  assert.deepEqual(f.restored, [{ id: 'one', epoch: 1 }]);
  assert.equal(f.removed.length, 0);
  assert.equal(f.panel.hasUnresolvedOperation(), false);
});
