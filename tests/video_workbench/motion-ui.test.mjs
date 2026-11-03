import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  createMotionPanel, createMotionRequest, createMotionAdoption, createMotionRenderRequest,
  currentMotionAudio, hasMotionScenes, usesDynamicVideo, createDynamicProjectRequest, motionMetadata, motionErrorLabel, motionSelection, validateMotionSegments,
} from '../../video_workbench/web/motion.mjs';
import { deliveryIssues, isActiveOperation, isUnresolvedProductionOperation } from '../../video_workbench/web/model.mjs';

const html = await readFile(new URL('../../video_workbench/web/index.html', import.meta.url), 'utf8');
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.listeners = new Map(); this.dataset = {};
    this.value = ''; this.checked = false; this.disabled = false; this.hidden = false;
    this.content = ''; this.paused = true; this.currentTime = 0;
  }
  set textContent(value) { this.content = String(value); this.children = []; }
  get textContent() { return [this.content, ...this.children.map(row => row.textContent)].join('\n'); }
  set innerHTML(value) { throw new Error(`Executable markup: ${value}`); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.content = ''; this.children = [...children]; }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
  async emit(name) { for (const callback of this.listeners.get(name) ?? []) await callback({ target: this }); }
  pause() { this.paused = true; }
  async play() { this.paused = false; }
  removeAttribute(name) { delete this[name]; }
}

function project(id = 'p1', revision = 1) {
  return { id, revision, speech_settings: { voice: 'one-project-voice' }, scenes: [
    { id: 'scene-1', title: '队列', narration: '先加入队列。然后处理任务。', visual_description: '队列到工作节点',
      head_version_id: null, head_audio_version_id: 'a1', audio_stale: false,
      audio_versions: [{ id: 'a1', sha256: '1'.repeat(64), duration_seconds: 4 }],
      motion: { mode: 'static', versions: [{ id: 'v1', stale: false, plan_sha256: '2'.repeat(64) }],
        head_version_id: null, head_stale: false, operation: null } },
  ] };
}
function version(id = 'v1') {
  return { id, stale: false, plan_sha256: id, plan: { template: 'flow',
    title: '<script>不要执行</script>', takeaway: '任务依次流转',
    elements: [{ id: 'queue', label: '<img onerror=alert(1)>队列', detail: '保存等待的任务' }],
    relations: [], actions: [{ beat_id: 'b1', target: 'queue', kind: 'reveal', placement: 'start', pace: 'normal' }] },
    beats: { segments: [{ id: 'b1', source_text: '先加入队列。', spoken_text: '先加入队列。' },
      { id: 'b2', source_text: '然后处理 SDK。', spoken_text: '然后处理艾斯迪开。' }] } };
}
function timing(id = 'v1', audioId = 'a1', revision = 1) {
  return { scene_id: 'scene-1', version_id: id, audio_version_id: audioId, project_revision: revision,
    beats: version(id).beats, audio: { id: audioId, duration_seconds: 4, sha256: '1'.repeat(64) },
    alignment: { method: 'estimated', review_status: 'pending', segments: [
      { beat_id: 'b1', start: 0, end: 2 }, { beat_id: 'b2', start: 2, end: 4 }] },
    alignment_sha256: '3'.repeat(64), requires_review: true };
}
function harness() {
  const nodes = new Map([...html.matchAll(/\bid="([^"]+)"/g)].map(match => [match[1], new Element()]));
  const document = { createElement: tag => new Element(tag), getElementById: id => {
    assert.ok(nodes.has(id), `Missing element ${id}`); return nodes.get(id);
  } };
  const p = project();
  const state = { context: { project: p, scene: p.scenes[0], epoch: 1, configured: true,
    enabled: true, renderConfigured: true, unsaved: false, projectBusy: false },
    data: structuredClone(p), calls: [], errors: [], successes: [], refreshes: 0, transport: null, refreshAction: null,
    timing: null, previewStates: [] };
  const fallback = async path => {
    if (path.endsWith('/motion')) return structuredClone(state.data);
    const match = path.match(/\/motion-versions\/([^/]+)(\/timing)?/);
    if (match) return structuredClone(match[2] ? state.timing ?? timing(decodeURIComponent(match[1]),
      new URL(`https://example.test${path}`).searchParams.get('audio_version_id'), state.data.revision)
      : version(decodeURIComponent(match[1])));
    throw new Error(`Unexpected request ${path}`);
  };
  const panel = createMotionPanel({ document, getContext: () => state.context,
    request: async (path, options = {}) => {
      state.calls.push({ path, options });
      return state.transport ? state.transport(path, options, fallback) : fallback(path);
    }, refresh: async () => { state.refreshes += 1; if (state.refreshAction) await state.refreshAction(); },
    onError: error => state.errors.push(error), onSuccess: message => state.successes.push(message),
    onPreviewStateChange: ready => state.previewStates.push(ready) });
  const setProject = (p, epoch = state.context.epoch) => {
    state.context = { ...state.context, project: p, scene: p.scenes[0], epoch }; state.data = structuredClone(p);
  };
  return { state, panel, setProject, $: id => nodes.get(id),
    load: () => panel.load(state.context.project.id, state.context.epoch) };
}
const posts = h => h.state.calls.filter(row => ['POST', 'PATCH'].includes(row.options.method));
const timingInput = (h, index, field = 0) => h.$('motion-timing-rows').children[index].children[field + 1].children[0];

test('generation, adoption and draft requests bind exact reviewed candidate, revision and adopted recording', () => {
  const p = project('../p?'), scene = p.scenes[0], candidate = scene.motion.versions[0];
  assert.deepEqual(createMotionRequest(p, scene, ' 连线 ', candidate), {
    path: '/api/scenes/scene-1/motion', body: { expected_revision: 1, instruction: '连线', base_version_id: 'v1' } });
  assert.deepEqual(createMotionAdoption(p, scene, candidate).body,
    { expected_revision: 1, version_id: 'v1', expected_head_version_id: null });
  assert.throws(() => createMotionAdoption(p, scene, candidate, { unsaved: true }), /未保存/);
  assert.throws(() => createMotionAdoption(p, scene, { ...candidate, stale: true }));
  assert.throws(() => createMotionAdoption(p, scene, { id: 'foreign' }));
  const draft = createMotionRenderRequest(p, { scene, candidate, audio: currentMotionAudio(scene) });
  assert.equal(draft.path, '/api/projects/..%2Fp%3F/motion-renders');
  assert.deepEqual(draft.body, { expected_revision: 1, purpose: 'draft', subtitles: false,
    scene_id: 'scene-1', motion_version_id: 'v1', audio_version_id: 'a1' });
  assert.deepEqual(createMotionRenderRequest(p, { purpose: 'delivery' }).body,
    { expected_revision: 1, purpose: 'delivery', subtitles: false });
  assert.throws(() => createMotionRenderRequest(p, { scene, candidate, audio: null }));
  assert.throws(() => createMotionRenderRequest(p, { scene, candidate: { ...candidate, stale: true }, audio: currentMotionAudio(scene) }));
});

test('timing validation rejects nonfinite, overlap, reordered, too short and out of recording segments', () => {
  const t = timing(), original = t.alignment.segments;
  assert.equal(validateMotionSegments(original, t), true);
  for (const alter of [rows => { rows[0].start = NaN; }, rows => { rows[0].end = Infinity; },
    rows => { rows[1].start = 1.99; }, rows => { rows[0].start = -0.01; },
    rows => { rows[1].end = 4.01; }, rows => { rows[0].end = 0.119; },
    rows => { rows.reverse(); }, rows => { rows[0].beat_id = 'foreign'; }, rows => { rows.pop(); }]) {
    const changed = structuredClone(original); alter(changed); assert.equal(validateMotionSegments(changed, t), false);
  }
});

test('review is inert text, first read only estimates and never generates, adopts, verifies or synthesizes', async () => {
  const h = harness(); await h.load();
  assert.equal(posts(h).length, 0);
  assert.match(h.$('motion-review').textContent, /<script>不要执行<\/script>/);
  assert.match(h.$('motion-review').textContent, /<img onerror=alert\(1\)>队列/);
  assert.match(h.$('motion-review').textContent, /第 1 段.*出现/);
  assert.match(h.$('motion-timing-status').textContent, /文字比例估算.*尚未人工核对/);
  assert.match(h.$('motion-timing-rows').textContent, /实际读法：然后处理艾斯迪开/);
  assert.equal(h.$('motion-timing-audio').src, '/api/audio-versions/a1/audio');
  assert.equal(h.$('adopt-motion').disabled, true); assert.equal(h.$('preview-motion').disabled, false);
  assert.deepEqual(h.state.context.project.speech_settings, { voice: 'one-project-voice' });
});

test('native candidate selection survives input then change and new candidates do not switch the review', async () => {
  const h = harness(); h.state.data.scenes[0].motion.versions.push({ id: 'v2', stale: false });
  await h.load(); assert.equal(h.$('motion-version').value, 'v2');
  h.$('motion-version').value = 'v1'; await h.$('motion-version').emit('input');
  assert.equal(h.$('motion-version').value, 'v1'); await h.$('motion-version').emit('change');
  const nativeOption = h.$('motion-version').children[0];
  h.panel.render(); assert.equal(h.$('motion-version').children[0], nativeOption);
  assert.equal(h.$('motion-version').value, 'v1');
  h.state.data.scenes[0].motion.versions.push({ id: 'v3', stale: false });
  h.$('confirm-motion-adoption').checked = true;
  await h.load(); assert.equal(h.$('motion-version').value, 'v1');
  assert.equal(motionSelection(h.state.data.scenes[0], 'missing').id, 'v3');
});

test('project review navigation selects an exact motion candidate without adopting or generating', async () => {
  const h = harness(); h.state.data.scenes[0].motion.versions.push({ id: 'v2', stale: false });
  await h.load(); assert.equal(h.$('motion-version').value, 'v2');
  h.panel.select('p1', 'scene-1', 'v1'); await h.load();
  assert.equal(h.$('motion-version').value, 'v1');
  assert.equal(h.state.context.scene.motion.head_version_id, null);
  assert.equal(posts(h).length, 0);
});

test('primary draft availability follows async timing, unsaved correction and scene selection', async () => {
  const h = harness(); h.panel.render();
  assert.equal(h.state.previewStates.at(-1), false);
  await h.load();
  assert.equal(h.state.previewStates.at(-1), true);
  const input = timingInput(h, 0, 1); input.value = '1.9'; await input.emit('input');
  assert.equal(h.state.previewStates.at(-1), false);
  await h.$('reset-motion-timing').emit('click');
  assert.equal(h.state.previewStates.at(-1), true);
  h.state.context.scene = null; h.panel.render();
  assert.equal(h.state.previewStates.at(-1), false);
  assert.equal(posts(h).length, 0);
});

test('adoption requires explicit confirmation, loaded current candidate and no dirty external changes', async () => {
  const h = harness(); await h.load(); await h.panel.adopt(); assert.equal(posts(h).length, 0);
  h.$('confirm-motion-adoption').checked = true; h.state.context.unsaved = true;
  await h.panel.adopt(); assert.equal(posts(h).length, 0);
  h.state.context.unsaved = false; h.state.context.project.revision = 2; h.panel.render();
  assert.equal(h.$('confirm-motion-adoption').checked, false);
  h.$('confirm-motion-adoption').checked = true; await h.panel.adopt(); assert.equal(posts(h).length, 0);
  h.state.data.revision = 2; await h.load(); h.$('confirm-motion-adoption').checked = true;
  h.state.transport = async () => project('p1', 3); await h.panel.adopt();
  assert.deepEqual(posts(h).at(-1), { path: '/api/scenes/scene-1/motion/adopt',
    options: { method: 'POST', body: { expected_revision: 2, version_id: 'v1', expected_head_version_id: null } } });
  assert.equal(h.state.context.scene.motion.mode, 'static'); // Only a server refresh changes adoption.
});

test('one generation submits once; uncertain result refreshes existing work without an automatic retry', async () => {
  const h = harness(); await h.load();
  h.$('motion-instruction').value = '先队列再节点'; await h.$('motion-instruction').emit('input');
  h.state.transport = async (path, options, fallback) => {
    if (options.method === 'POST') {
      h.state.data.scenes[0].motion.operation = { id: 'original', status: 'submitting' };
      throw new Error('response lost');
    }
    return fallback(path);
  };
  h.state.refreshAction = h.load;
  await Promise.all([h.panel.generate(), h.panel.generate()]);
  assert.equal(posts(h).length, 1); assert.equal(h.state.refreshes, 1);
  assert.equal(h.panel.hasActiveOperation(), true); assert.equal(h.$('cancel-motion').hidden, false);
  assert.equal(h.$('generate-motion').disabled, true); assert.equal(h.state.errors.length, 1);
  assert.equal(h.$('motion-instruction').value, '先队列再节点');
  assert.ok(!h.state.calls.some(row => /audio\/generate|\/adopt|motion-renders/.test(row.path)));
});

test('gate off preserves history, audio and cancellation and permits withdrawing to static only', async () => {
  const h = harness(); h.state.context.enabled = false; h.state.context.configured = false; h.state.context.renderConfigured = false;
  const motion = h.state.data.scenes[0].motion;
  motion.operation = { id: '../op', status: 'running' }; motion.mode = 'motion'; motion.head_version_id = 'v1';
  await h.load(); assert.equal(h.$('motion-panel').hidden, false); assert.equal(h.panel.hasActiveOperation(), true);
  assert.equal(h.$('motion-use-static').disabled, false); assert.equal(h.$('cancel-motion').hidden, false);
  assert.equal(h.$('generate-motion').disabled, true); assert.equal(timingInput(h, 0).disabled, true);
  h.$('confirm-motion-adoption').checked = true; h.$('confirm-motion-timing').checked = true;
  const calls = h.state.calls.length;
  await h.panel.generate(); await h.panel.adopt(); await h.panel.saveTiming(true); await h.panel.preview(); await h.panel.mode('motion');
  assert.equal(h.state.calls.length, calls);
  h.state.transport = async () => ({ id: '../op', status: 'cancelling' }); await h.panel.cancel();
  assert.equal(posts(h).at(-1).path, '/api/motion-operations/..%2Fop/cancel');
  assert.equal(h.$('cancel-motion').disabled, true);
  const cancelledCalls = posts(h).length; await h.panel.cancel(); assert.equal(posts(h).length, cancelledCalls);
  h.state.transport = async () => project(); await h.panel.mode('static');
  assert.deepEqual(posts(h).at(-1).options.body, { expected_revision: 1, mode: 'static' });
});

test('disconnecting Agent blocks new generation but existing candidates can be adopted and timed', async () => {
  const h = harness(); h.state.context.configured = false; await h.load();
  assert.equal(h.$('generate-motion').disabled, true); assert.equal(timingInput(h, 0).disabled, false);
  h.$('confirm-motion-adoption').checked = true; h.panel.render();
  assert.equal(h.$('adopt-motion').disabled, false); assert.equal(h.$('preview-motion').disabled, false);
});

test('manual save remains pending and verification is a separate explicit recording-bound action', async () => {
  const h = harness(); await h.load(); await h.panel.saveTiming(true); assert.equal(posts(h).length, 0);
  const input = timingInput(h, 1); input.value = '2.25'; await input.emit('input');
  assert.equal(h.panel.hasDrafts(), true); assert.equal(h.panel.hasProjectDrafts('p1'), true);
  assert.equal(h.$('verify-motion-timing').disabled, true); assert.equal(h.$('preview-motion').disabled, true);
  h.$('motion-correction-reason').value = '按换气校正'; await h.$('motion-correction-reason').emit('input');
  h.state.transport = async (path, options, fallback) => {
    if (options.method) {
      h.state.data.revision += 1; h.state.context.project.revision = h.state.data.revision;
      h.state.timing = timing('v1', 'a1', h.state.data.revision);
      h.state.timing.alignment.method = 'manual';
      h.state.timing.alignment.review_status = options.method === 'PATCH' ? 'pending' : 'verified';
      h.state.timing.alignment.segments = options.body.segments ?? h.state.timing.alignment.segments;
      h.state.timing.alignment_sha256 = options.method === 'PATCH' ? '4'.repeat(64) : '5'.repeat(64);
      return h.state.timing;
    }
    return fallback(path);
  };
  h.state.refreshAction = h.load;
  await h.panel.saveTiming();
  assert.equal(posts(h).length, 1); assert.equal(posts(h)[0].options.method, 'PATCH');
  assert.deepEqual(posts(h)[0].options.body, { expected_revision: 1, audio_version_id: 'a1',
    expected_alignment_sha256: '3'.repeat(64), correction_reason: '按换气校正',
    segments: [{ beat_id: 'b1', start: 0, end: 2 }, { beat_id: 'b2', start: 2.25, end: 4 }] });
  assert.match(h.$('motion-timing-status').textContent, /手工时序已保存.*尚未完成试听核对/);
  assert.equal(h.panel.hasDrafts(), false); await h.panel.saveTiming(true); assert.equal(posts(h).length, 1);
  h.$('confirm-motion-timing').checked = true; await h.$('confirm-motion-timing').emit('change');
  await h.panel.saveTiming(true);
  assert.deepEqual(posts(h).at(-1), { path: '/api/motion-versions/v1/timing/verify', options: { method: 'POST',
    body: { expected_revision: 2, audio_version_id: 'a1', expected_alignment_sha256: '4'.repeat(64) } } });
  assert.match(h.$('motion-timing-status').textContent, /已由人工核对/);
  assert.equal(posts(h).length, 2); assert.ok(!h.state.calls.some(row => /speech|audio\/generate/.test(row.path)));
});

test('timing draft survives polling, scene and project switches; late reads cannot restore old review', async () => {
  const h = harness(); await h.load(); const first = timingInput(h, 0, 1);
  first.value = '1.8'; await first.emit('input'); await h.load();
  assert.equal(timingInput(h, 0, 1).value, '1.8');
  const other = structuredClone(h.state.data.scenes[0]); other.id = 'scene-2'; other.motion.versions = [];
  h.state.data.scenes.push(other); h.state.context.project.scenes.push(other); h.state.context.scene = other;
  await h.load(); assert.equal(h.$('motion-timing-rows').children.length, 0);
  h.state.context.scene = h.state.context.project.scenes[0]; await h.load();
  assert.equal(timingInput(h, 0, 1).value, '1.8');
  let finishOld;
  h.state.transport = () => new Promise(resolve => { finishOld = resolve; }); const old = h.load();
  h.setProject(project('p2'), 2); h.state.transport = null; await h.load();
  h.setProject(project(), 3); await h.load(); finishOld(project('p1', 9)); await old;
  assert.equal(timingInput(h, 0, 1).value, '1.8'); assert.equal(h.panel.hasProjectDrafts('p1'), true);
  assert.equal(h.$('motion-version').value, 'v1');
});

test('current recording or stale plan change clears verification and does not reuse the old timing', async () => {
  const h = harness(); h.state.timing = timing(); h.state.timing.alignment.review_status = 'verified'; await h.load();
  h.$('confirm-motion-timing').checked = true;
  const scene = h.state.context.scene; scene.head_audio_version_id = 'a2';
  scene.audio_versions.push({ id: 'a2', duration_seconds: 4 }); h.state.data = structuredClone(h.state.context.project);
  h.state.timing = timing('v1', 'a2'); h.panel.render();
  assert.equal(h.$('confirm-motion-timing').checked, false); assert.equal(h.$('motion-timing-rows').children.length, 0);
  await h.load(); assert.match(h.$('motion-timing-status').textContent, /尚未人工核对/);
  assert.equal(h.$('motion-timing-audio').src, '/api/audio-versions/a2/audio');
  h.state.data.scenes[0].motion.versions[0].stale = true; await h.load();
  assert.equal(h.$('motion-timing-rows').children.length, 0); assert.equal(h.$('preview-motion').disabled, true);
  assert.match(h.$('motion-timing-status').textContent, /历史方案不能校正/);
});

test('changed saved timing or project revision keeps local edits and blocks stale writes until explicit reload', async () => {
  const h = harness(); await h.load(); const input = timingInput(h, 1);
  input.value = '2.3'; await input.emit('input');
  h.state.context.project.revision = 2; h.state.data.revision = 2;
  h.state.timing = timing('v1', 'a1', 2); h.state.timing.alignment_sha256 = '4'.repeat(64);
  await h.load(); assert.equal(timingInput(h, 1).value, '2.3');
  assert.equal(h.$('save-motion-timing').disabled, true); assert.equal(h.$('verify-motion-timing').disabled, true);
  assert.match(h.$('motion-timing-note').textContent, /项目或保存时序已变化.*本地校正仍保留/);
  await h.panel.saveTiming(); assert.equal(posts(h).length, 0);
  await h.$('reset-motion-timing').emit('click');
  assert.equal(h.panel.hasDrafts(), false); assert.equal(timingInput(h, 1).value, '2');
});

test('orphan recording drafts remain visible as unsaved and can be explicitly discarded for this scene', async () => {
  const h = harness(); await h.load(); const input = timingInput(h, 1);
  input.value = '2.2'; await input.emit('input');
  h.state.context.scene.head_audio_version_id = 'a2'; h.state.context.scene.audio_versions.push({ id: 'a2', duration_seconds: 4 });
  h.state.data = structuredClone(h.state.context.project); await h.load();
  assert.equal(h.panel.hasProjectDrafts('p1'), true);
  assert.match(h.$('motion-timing-note').textContent, /其他配音或候选仍有未保存校正/);
  h.state.context.scene.audio_stale = true; h.state.data = structuredClone(h.state.context.project); await h.load();
  assert.equal(h.$('reset-motion-timing').disabled, false);
  await h.$('reset-motion-timing').emit('click'); assert.equal(h.panel.hasProjectDrafts('p1'), false);
});

test('busy timing writes disable inputs and older callbacks cannot overwrite a saved draft', async () => {
  const h = harness(); await h.load(); const input = timingInput(h, 1);
  input.value = '2.2'; await input.emit('input'); let finish;
  h.state.transport = () => new Promise(resolve => { finish = resolve; });
  const save = h.panel.saveTiming(); assert.equal(timingInput(h, 1).disabled, true);
  input.value = '3'; await input.emit('input');
  finish(timing()); await save;
  assert.equal(posts(h)[0].options.body.segments[1].start, 2.2);
  assert.equal(h.panel.hasDrafts(), false);
});

test('candidate MP4 draft uses saved estimated timing without adoption, verification or TTS', async () => {
  const h = harness(); await h.load(); h.state.transport = async () => ({ id: 'draft', status: 'queued' });
  await h.panel.preview(); assert.equal(posts(h).length, 1);
  assert.deepEqual(posts(h)[0], { path: '/api/projects/p1/motion-renders', options: { method: 'POST',
    body: { expected_revision: 1, purpose: 'draft', subtitles: false,
      scene_id: 'scene-1', motion_version_id: 'v1', audio_version_id: 'a1' } } });
  assert.equal(h.state.context.scene.motion.head_version_id, null); assert.equal(h.state.refreshes, 1);
});

test('audio segment audition starts and stops at the selected actual recording segment', async () => {
  const h = harness(); await h.load();
  await h.$('motion-timing-rows').children[1].children.at(-1).emit('click');
  const player = h.$('motion-timing-audio'); assert.equal(player.currentTime, 2); assert.equal(player.paused, false);
  player.currentTime = 4; await player.emit('timeupdate'); assert.equal(player.paused, true);
});

test('static delivery readiness stays intact and mixed projects use only their chosen visual mode', () => {
  const p = project(); assert.equal(hasMotionScenes(p), false);
  assert.deepEqual(deliveryIssues(p, { subtitles: false })[0].reasons, ['missing_visual']);
  p.scenes[0].head_version_id = 'static-image'; assert.deepEqual(deliveryIssues(p, { subtitles: false }), []);
  p.scenes[0].motion.mode = 'motion'; assert.equal(hasMotionScenes(p), true);
  assert.deepEqual(deliveryIssues(p, { subtitles: false })[0].reasons, ['missing_motion_plan']);
  p.scenes[0].head_version_id = null; p.scenes[0].motion.head_version_id = 'v1';
  assert.deepEqual(deliveryIssues(p, { subtitles: false }), []);
  p.scenes[0].motion.head_stale = true;
  assert.deepEqual(deliveryIssues(p, { subtitles: false })[0].reasons, ['stale_motion']);
});

test('read failures retain review and drafts, error names stay inert and nontechnical', async () => {
  const h = harness(); await h.load(); const original = h.$('motion-review').textContent;
  const input = timingInput(h, 0, 1); input.value = '1.9'; await input.emit('input');
  h.state.transport = async () => { throw new Error('offline'); }; await h.load();
  assert.equal(h.$('motion-review').textContent, original); assert.equal(timingInput(h, 0, 1).value, '1.9');
  assert.equal(h.$('motion-read-error').hidden, false);
  for (const code of ['motion_disabled', 'motion_audio_binding_mismatch', 'submission_uncertain', '__proto__', 'constructor']) {
    assert.ok(motionErrorLabel(code)); assert.ok(!motionErrorLabel(code).includes(code));
  }
});

test('all controls exist once; app wires motion reads, MP4 playback, polling and unload drafts', async () => {
  const source = await readFile(new URL('../../video_workbench/web/motion.mjs', import.meta.url), 'utf8');
  const app = await readFile(new URL('../../video_workbench/web/app.mjs', import.meta.url), 'utf8');
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  for (const [, id] of source.matchAll(/\$\('([^']+)'\)/g)) assert.ok(ids.includes(id), id);
  assert.doesNotMatch(source, /innerHTML|insertAdjacentHTML|eval\(/);
  assert.match(app, /motionPanel\.load\(ticket.projectId, ticket.epoch\)/);
  assert.match(app, /!motionPanel\.hasActiveOperation\(state.projectId, state.project\?\.scenes\)/); assert.match(app, /motionPanel\.hasDrafts\(\)/);
  assert.match(app, /createMotionRenderRequest\(state.project, \{ purpose: 'delivery' \}\)/);
  assert.match(app, /function openMotionRender\(item\)/);
  assert.match(app, /!\$\('motion-panel'\)\.contains\(event.target\) && !\$\('motion-timing-section'\)\.contains\(event.target\)/);
  assert.match(html, /id="motion-render-video"[^>]*controls/);
  assert.doesNotMatch(html, /id="render-subtitles"[^>]*checked/);
});

test('a dynamic project keeps the video workflow before any plan exists without adopting a candidate', () => {
  const p = project(); p.production_mode = 'motion'; p.scenes[0].motion.mode = 'motion';
  p.scenes[0].motion.versions = [];
  const original = structuredClone(p);
  assert.equal(usesDynamicVideo(p), true);
  assert.deepEqual(deliveryIssues(p, { subtitles: false })[0].reasons, ['missing_motion_plan']);
  assert.deepEqual(p, original);
  const legacy = project(); legacy.scenes[0].head_version_id = 'old-static';
  assert.equal(usesDynamicVideo(legacy), false);
  legacy.scenes[0].motion.mode = 'motion';
  assert.equal(usesDynamicVideo(legacy), true);
});

test('explicit transition binds the reviewed project revision and rejects unsaved or active work', () => {
  const p = project('../legacy?');
  assert.deepEqual(createDynamicProjectRequest(p), {
    path: '/api/projects/..%2Flegacy%3F/production-mode', body: { mode: 'motion', expected_revision: 1 },
  });
  assert.throws(() => createDynamicProjectRequest(p, { unsaved: true }), /未保存/);
  assert.throws(() => createDynamicProjectRequest(p, { busy: true }), /任务结束/);
  assert.throws(() => createDynamicProjectRequest(null));
  assert.equal(p.production_mode, undefined);
  assert.equal(p.scenes[0].head_version_id, null);
});

test('transition waits for uncertain and unknown visible work without changing polling semantics', async () => {
  const p = project();
  const uncertain = { id: 'unconfirmed', status: 'uncertain' };
  assert.equal(isActiveOperation(uncertain), false);
  assert.equal(isUnresolvedProductionOperation(uncertain), true);
  for (const field of ['operation', 'audio_operation']) {
    p.scenes[0][field] = uncertain;
    assert.throws(() => createDynamicProjectRequest(p), /任务结束/);
    delete p.scenes[0][field];
  }
  p.storyboard_operation = { id: 'new-status', status: 'unknown' };
  assert.throws(() => createDynamicProjectRequest(p), /任务结束/);
  p.storyboard_operation.status = 'completed';
  assert.ok(createDynamicProjectRequest(p));
  const h = harness(); h.state.data.scenes[0].motion.operation = uncertain;
  await h.load();
  assert.equal(h.panel.hasActiveOperation(), false);
  assert.equal(h.panel.hasUnresolvedOperation(), true);
  assert.equal(posts(h).length, 0);
});

test('raw scene intent remains pending motion while an explicit static choice takes priority', async () => {
  const p = project(); p.scenes[0].production_mode = 'motion';
  delete p.scenes[0].motion;
  assert.equal(hasMotionScenes(p), true);
  assert.equal(motionMetadata(p.scenes[0]).head_version_id, null);
  assert.deepEqual(deliveryIssues(p, { subtitles: false })[0].reasons, ['missing_motion_plan']);
  const h = harness(); h.setProject(p); await h.load();
  assert.equal(h.$('motion-mode-label').textContent, '动态图解待制作');
  p.scenes[0].motion = { mode: 'static', head_version_id: null, versions: [] };
  p.scenes[0].head_version_id = 'legacy-static'; h.setProject(p); await h.load();
  assert.equal(hasMotionScenes(p), false);
  assert.equal(motionMetadata(p.scenes[0]).mode, 'static');
  assert.deepEqual(deliveryIssues(p, { subtitles: false }), []);
  assert.match(h.$('motion-mode-label').textContent, /当前复用静态素材/);
});

test('pending dynamic intent is shown as unmade instead of adopted and has no timing section', async () => {
  const h = harness(); const p = project(); p.production_mode = 'motion';
  p.scenes[0].motion.mode = 'motion'; p.scenes[0].motion.versions = [];
  h.setProject(p); await h.load();
  assert.equal(h.$('motion-mode-label').textContent, '动态图解待制作');
  assert.equal(h.$('motion-timing-section').hidden, true);
  assert.equal(h.$('adopt-motion').disabled, true);
  assert.equal(h.$('preview-motion').disabled, true);
  assert.equal(posts(h).length, 0);
});
