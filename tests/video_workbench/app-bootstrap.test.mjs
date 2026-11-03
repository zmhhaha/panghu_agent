import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { setImmediate as nextTurn } from 'node:timers/promises';

const html = await readFile(new URL('../../video_workbench/web/index.html', import.meta.url), 'utf8');

// Run the actual application entrypoint and all three real panels together.
// Individual panel tests inject an already-built context and cannot expose a
// cycle between the application's context builders and panel status queries.
class Element {
  constructor(tag = 'div') {
    this.tagName = tag; this.children = []; this.listeners = new Map(); this.dataset = {};
    this.value = ''; this.content = ''; this.hidden = false; this.disabled = false;
    this.checked = false; this.open = false; this.paused = true;
    const classes = new Set();
    this.classList = { toggle(name, enabled) { enabled ? classes.add(name) : classes.delete(name); } };
  }
  set textContent(value) { this.content = String(value); this.children = []; }
  get textContent() { return [this.content, ...this.children.map(row => row.textContent)].join('\n'); }
  set innerHTML(value) { throw new Error(`Unexpected executable markup: ${value}`); }
  append(...rows) { this.children.push(...rows.flatMap(row => row.tagName === '#fragment' ? row.children : [row])); }
  replaceChildren(...rows) { this.content = ''; this.children = []; this.append(...rows); }
  setAttribute(name, value) { this[name] = value; }
  removeAttribute(name) { delete this[name]; }
  addEventListener(name, callback) {
    if (!this.listeners.has(name)) this.listeners.set(name, []);
    this.listeners.get(name).push(callback);
  }
  async emit(name) {
    for (const callback of this.listeners.get(name) ?? []) await callback({ target: this, preventDefault() {} });
  }
  pause() { this.paused = true; }
  async play() { this.paused = false; await this.emit('play'); }
  load() { this.paused = true; }
  showModal() { this.open = true; }
  close() { this.open = false; }
  contains(target) { return this === target || this.children.some(child => child.contains(target)); }
  scrollIntoView() {}
}

function project(id, mode = 'motion') {
  return { id, title: `项目 ${id}`, script: '先说明问题，再解释原因。', revision: 1,
    production_mode: mode, speech_settings: { voice: 'test-voice' }, scenes: [
      { id: `${id}-scene`, title: `镜头 ${id}`, narration: '先说明问题。', visual_description: '逐步展开',
        format: 'svg', duration_seconds: 5, versions: [], audio_versions: [],
        head_version_id: null, head_audio_version_id: null, production_mode: mode,
        motion: { mode, versions: [], head_version_id: null, operation: null } },
    ] };
}

test('real app bootstrap, project switches and cross-panel busy checks never rebuild each other recursively', async t => {
  const originals = Object.fromEntries(['document', 'window', 'fetch', 'setTimeout', 'clearTimeout',
    'requestAnimationFrame', 'cancelAnimationFrame']
    .map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
  const nodes = new Map([...html.matchAll(/<([\w-]+)\b[^>]*\bid="([^"]+)"[^>]*>/g)].map(match => {
    const node = new Element(match[1]); node.hidden = /\bhidden\b/.test(match[0]);
    return [match[2], node];
  }));
  const $ = id => { assert.ok(nodes.has(id), `Missing real application element ${id}`); return nodes.get(id); };
  const pageEvents = new Element(), windowEvents = new Element(), calls = [], timers = new Map(), frames = new Map();
  let timerSequence = 0, frameSequence = 0, releaseMotionRead, delayMotionRead = true;
  const idle = project('idle'), motionBusy = project('motion-busy'), explanationBusy = project('explanation-busy');
  const batchBusy = project('batch-busy'), historical = project('historical', 'static');
  const audition = project('audition');
  const auditionScene = audition.scenes[0];
  const auditionAudio = { id: 'audition-audio', number: 1, sha256: 'a'.repeat(64), duration_seconds: 4 };
  auditionScene.audio_versions = [auditionAudio]; auditionScene.head_audio_version_id = auditionAudio.id;
  auditionScene.motion.versions = [{ id: 'audition-motion', number: 1, plan_sha256: 'b'.repeat(64) }];
  const auditionVersion = { id: 'audition-motion', plan: { template: 'flow', title: '逐段试听',
    takeaway: '每段单独核对', elements: [{ id: 'one', label: '第一步', detail: '先说明问题' }],
    relations: [], actions: [] }, beats: { segments: [
      { id: 'first', source_text: '先说明问题。', spoken_text: '先说明问题。' },
      { id: 'second', source_text: '再解释原因。', spoken_text: '再解释原因。' },
    ] } };
  motionBusy.scenes[0].motion.operation = { id: 'motion-op', status: 'running' };
  const all = [idle, motionBusy, explanationBusy, batchBusy, historical, audition];
  let listedProjects = [];
  const batch = { id: 'batch-op', project_id: batchBusy.id, status: 'running', items: [] };
  const settings = { user_id: 'offline-bootstrap-test', csrf_token: 'offline-only',
    motion_enabled: true, motion_configured: true, motion_render_configured: true,
    batch_enabled: true, explanation_configured: true, speech_configured: true,
    speech_voice: 'test-voice', speech_voices: [{ id: 'test-voice', name: '测试音色', default: true }] };
  const fetchFixture = async (path, options) => {
    assert.equal(options.method, 'GET', `Bootstrap and switching must never submit: ${path}`);
    calls.push(path);
    let result;
    if (path === '/api/session') result = settings;
    else if (path === '/api/projects') result = { projects: listedProjects.map(p => ({ id: p.id, title: p.title, scene_count: p.scenes.length })) };
    else if (path === '/api/batches/batch-op') result = batch;
    else if (path === '/api/motion-versions/audition-motion') result = auditionVersion;
    else if (path === '/api/motion-versions/audition-motion/timing?audio_version_id=audition-audio') result = {
      scene_id: auditionScene.id, version_id: auditionVersion.id, audio_version_id: auditionAudio.id,
      project_revision: audition.revision, beats: auditionVersion.beats, audio: auditionAudio,
      alignment: { method: 'estimated', review_status: 'pending', segments: [
        { beat_id: 'first', start: 0, end: 2 }, { beat_id: 'second', start: 2, end: 4 }] },
      alignment_sha256: 'c'.repeat(64), requires_review: true,
    };
    else {
      const [, id, suffix] = path.match(/^\/api\/projects\/([^/]+)(?:\/(.*))?$/) ?? [];
      const p = all.find(row => row.id === id);
      assert.ok(p, `Unexpected application request ${path}`);
      if (!suffix) result = p;
      else if (suffix === 'renders') result = { renders: [] };
      else if (suffix.startsWith('timeline?')) result = { duration_seconds: 5, scenes: [
        { id: p.scenes[0].id, title: p.scenes[0].title, start_seconds: 0, duration_seconds: 5,
          leading_seconds: 0.5, audio_duration_seconds: 4, audio_version_id: 'historical-audio',
          visual_version_id: 'historical-visual', cues: [] },
      ] };
      else if (suffix === 'motion') {
        if (id === motionBusy.id && delayMotionRead) {
          delayMotionRead = false; await new Promise(resolve => { releaseMotionRead = resolve; });
        }
        result = p;
      } else if (suffix === 'explanation') result = { versions: [],
        operation: id === explanationBusy.id ? { id: 'explanation-op', status: 'running' } : null };
      else if (suffix === 'batches') result = { batches: id === batchBusy.id ? [batch] : [] };
      else assert.fail(`Unexpected application request ${path}`);
    }
    return { ok: true, status: 200, json: async () => structuredClone(result) };
  };
  const clickProject = id => {
    const button = $('project-list').children.find(row => row.dataset.projectId === id);
    assert.ok(button, `Project selector ${id}`); return button.emit('click');
  };
  const healthy = id => {
    assert.equal($('global-error').hidden, true, $('error-text').textContent);
    assert.equal($('loading-view').hidden, true);
    assert.equal($('workspace').hidden, false);
    assert.equal($('project-title').textContent, `项目 ${id}`);
    assert.equal($('scene-title').textContent, `镜头 ${id}`);
  };
  try {
    Object.assign(globalThis, {
      document: { hidden: false, getElementById: $, createElement: tag => new Element(tag),
        createDocumentFragment: () => new Element('#fragment'),
        addEventListener: (...args) => pageEvents.addEventListener(...args) },
      window: { addEventListener: (...args) => windowEvents.addEventListener(...args) },
      fetch: fetchFixture,
      setTimeout: (callback, duration) => { const id = ++timerSequence; timers.set(id, { callback, duration }); return id; },
      clearTimeout: id => timers.delete(id),
      requestAnimationFrame: callback => { const id = ++frameSequence; frames.set(id, callback); return id; },
      cancelAnimationFrame: id => frames.delete(id),
    });
    await import('../../video_workbench/web/app.mjs?bootstrap-integration');
    await nextTurn();
    assert.equal($('welcome').hidden, false, 'An empty workspace must also render without context recursion');
    assert.equal($('global-error').hidden, true, $('error-text').textContent);
    listedProjects = all;
    await $('refresh-all').emit('click');
    healthy(idle.id);
    assert.equal($('session-label').textContent, '已连接工作台');
    assert.equal($('generate-motion').disabled, false);
    assert.equal($('generate-explanation').disabled, false);
    assert.equal($('preview-batch').disabled, false);

    const selectingMotion = clickProject(motionBusy.id);
    await nextTurn();
    healthy(motionBusy.id);
    assert.equal(typeof releaseMotionRead, 'function');
    assert.equal($('preview-batch').disabled, true, 'Project detail blocks batch before motion metadata arrives');
    releaseMotionRead(); await selectingMotion;
    assert.equal($('generate-motion').disabled, true);
    assert.equal($('convert-project-motion').disabled, true);

    await clickProject(explanationBusy.id);
    healthy(explanationBusy.id);
    assert.equal($('generate-motion').disabled, true, 'Explanation work still blocks competing motion work');
    assert.equal($('preview-batch').disabled, true);

    await clickProject(batchBusy.id);
    healthy(batchBusy.id);
    await $('toggle-scene-editor').emit('click');
    assert.equal($('save-scene').disabled, true);
    assert.equal($('generate-audio').disabled, true);
    assert.equal($('generate-explanation').disabled, true);
    assert.equal($('generate-motion').disabled, true);
    assert.equal($('preview-batch').disabled, true);

    await clickProject(historical.id);
    healthy(historical.id);
    assert.equal($('batch-panel').hidden, true);
    assert.equal($('generate-explanation').disabled, false, 'Previous project activity cannot leak into the new project');
    assert.equal($('convert-project-motion').disabled, false);
    motionBusy.scenes[0].motion.operation.status = 'uncertain';
    await clickProject(motionBusy.id);
    healthy(motionBusy.id);
    assert.equal($('convert-project-motion').disabled, true, 'An uncertain operation remains unresolved');
    assert.equal($('preview-batch').disabled, true);
    assert.equal(timers.size, 0, 'Uncertain work does not become a running-operation polling loop');
    await clickProject(idle.id);
    healthy(idle.id);
    assert.equal($('generate-motion').disabled, false);
    assert.equal($('preview-batch').disabled, false);
    await $('refresh-all').emit('click');
    healthy(idle.id);
    assert.ok(calls.includes('/api/projects/batch-busy/batches'));
    assert.ok(calls.includes('/api/projects/explanation-busy/explanation'));
    const mediaIds = [...html.matchAll(/<(?:audio|video)\b[^>]*\bid="([^"]+)"/g)].map(match => match[1]);
    const pauseAll = () => mediaIds.forEach(id => $(id).pause());
    await t.test('candidate narration and timing audition stop each other when native controls start playback', async () => {
      for (const [first, second] of [['audio-preview', 'motion-timing-audio'], ['motion-timing-audio', 'audio-preview']]) {
        pauseAll(); await $(first).play(); await $(second).play();
        assert.equal($(first).paused, true, `${second} must pause ${first}`);
        assert.equal($(second).paused, false, 'Starting media must keep playing');
      }
    });
    await t.test('all native media controls keep only the latest player active in every ordered pair', async () => {
      assert.equal(mediaIds.length, 5, 'Cover every native audio and video player in the real page');
      for (const first of mediaIds) for (const second of mediaIds) {
        if (first === second) continue;
        pauseAll(); await $(first).play(); await $(second).play();
        for (const id of mediaIds) assert.equal($(id).paused, id !== second,
          `${first} followed by ${second}: ${id} playback state`);
      }
      pauseAll();
    });
    await t.test('switching projects still stops every native media player', async () => {
      for (const id of mediaIds) {
        pauseAll(); await clickProject(idle.id); await $(id).play();
        assert.equal($(id).paused, false);
        await clickProject(historical.id); healthy(historical.id);
        for (const other of mediaIds) assert.equal($(other).paused, true, `Switch must stop ${other} after ${id}`);
      }
    });
    await t.test('another player stops project preview during silence and speech without stopping its own playback', async () => {
      pauseAll(); await clickProject(historical.id);
      await $('preview-project').emit('click'); await nextTurn();
      assert.equal($('project-player-dialog').open, true);
      await $('project-player-toggle').emit('click');
      assert.equal($('project-player-audio').paused, true, 'The leading silence has no native audio playing');
      assert.equal(frames.size, 1, 'Project preview is still advancing through its leading silence');
      await $('audio-preview').play();
      assert.equal(frames.size, 0, 'Competing media must also stop the project animation loop');
      assert.equal($('project-player-toggle').textContent, '播放');
      assert.equal($('audio-preview').paused, false);
      await $('project-player-toggle').emit('click');
      $('project-player-seek').valueAsNumber = 1; await $('project-player-seek').emit('input');
      assert.equal($('project-player-audio').paused, false, 'Native project play must preserve its own controller');
      assert.equal($('project-player-toggle').textContent, '暂停');
      assert.equal($('audio-preview').paused, true);
      await $('motion-timing-audio').play();
      assert.equal($('project-player-audio').paused, true);
      assert.equal($('project-player-toggle').textContent, '播放');
      assert.equal(frames.size, 0);
      await clickProject(idle.id);
      assert.equal($('project-player-dialog').open, false);
      assert.equal($('project-player-audio').src, undefined);
      assert.equal(frames.size, 0);
    });
    await t.test('timing segment audition keeps its boundary until another native player takes over', async () => {
      await clickProject(audition.id); healthy(audition.id);
      assert.equal($('motion-timing-rows').children.length, 2);
      const listen = $('motion-timing-rows').children[0].children.at(-1);
      const audio = $('motion-timing-audio');
      await listen.emit('click');
      audio.currentTime = 1; await audio.emit('timeupdate'); assert.equal(audio.paused, false);
      audio.currentTime = 2; await audio.emit('timeupdate'); assert.equal(audio.paused, true,
        'The native play handler must not clear its own segment end');
      await listen.emit('click'); await $('audio-preview').play();
      assert.equal(audio.paused, true);
      await audio.play(); audio.currentTime = 3; await audio.emit('timeupdate');
      assert.equal(audio.paused, false, 'Taking over with another player clears the old segment boundary');
      assert.equal($('audio-preview').paused, true);
    });
    await windowEvents.emit('pagehide');
    assert.equal(timers.size, 0, 'Project switch and page teardown clear polling');
  } finally {
    for (const [key, descriptor] of Object.entries(originals)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  }
});
