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
  reportValidity() { return true; }
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
  const auditionAudio = { id: 'audition-audio', number: 1, sha256: 'a'.repeat(64), duration_seconds: 4,
    voice: 'test-voice', source: 'tts', operation_id: 'original-audio-op', stale: false };
  auditionScene.audio_versions = [auditionAudio]; auditionScene.head_audio_version_id = auditionAudio.id;
  auditionScene.motion.versions = [{ id: 'audition-motion', number: 1, plan_sha256: 'b'.repeat(64) }];
  const auditionVersion = { id: 'audition-motion', plan: { template: 'flow', title: '逐段试听',
    takeaway: '每段单独核对', elements: [{ id: 'one', label: '第一步', detail: '先说明问题' }],
    relations: [], actions: [] }, beats: { segments: [
      { id: 'first', source_text: '先说明问题。', spoken_text: '先说明问题。' },
      { id: 'second', source_text: '再解释原因。', spoken_text: '再解释原因。' },
    ] } };
  auditionScene.motion.head_version_id = auditionVersion.id;
  const secondDraftScene = structuredClone(auditionScene);
  secondDraftScene.id = 'audition-second-scene'; secondDraftScene.title = '第二个已采用镜头';
  secondDraftScene.audio_versions[0].id = 'audition-second-audio';
  secondDraftScene.head_audio_version_id = 'audition-second-audio';
  secondDraftScene.motion.versions[0].id = 'audition-second-motion';
  secondDraftScene.motion.head_version_id = 'audition-second-motion';
  audition.scenes.push(secondDraftScene);
  motionBusy.scenes[0].motion.operation = { id: 'motion-op', status: 'running' };
  const all = [idle, motionBusy, explanationBusy, batchBusy, historical, audition];
  let listedProjects = [];
  const batch = { id: 'batch-op', project_id: batchBusy.id, status: 'running', items: [] };
  const settings = { user_id: 'offline-bootstrap-test', csrf_token: 'offline-only',
    motion_enabled: true, motion_configured: true, motion_render_configured: true,
    batch_enabled: true, explanation_configured: true, speech_configured: true,
    speech_voice: 'test-voice', speech_voices: [{ id: 'test-voice', name: '测试音色', default: true },
      { id: 'other-voice', name: '另一音色' }] };
  const writes = [], voiceWrites = [], sceneWrites = [];
  let allowProjectDraft = false, allowVoiceSave = false, allowSceneSave = false;
  const fetchFixture = async (path, options) => {
    calls.push(path);
    if (options.method !== 'GET') {
      if (allowSceneSave && path === '/api/scenes/audition-scene' && options.method === 'PATCH') {
        const body = JSON.parse(options.body);
        sceneWrites.push(body);
        const { expected_revision, base_fields, ...fields } = body;
        Object.assign(auditionScene, fields); audition.revision++;
        return { ok: true, status: 200, json: async () => structuredClone(audition) };
      }
      if (allowVoiceSave && path === '/api/projects/audition/speech' && options.method === 'PATCH') {
        const body = JSON.parse(options.body);
        assert.equal(body.expected_revision, audition.revision);
        assert.equal(body.voice, 'other-voice');
        voiceWrites.push(body);
        audition.speech_settings.voice = body.voice; audition.revision++;
        for (const scene of audition.scenes) {
          scene.audio_versions.forEach(audio => { audio.stale = true; }); scene.audio_stale = true;
        }
        return { ok: true, status: 200, json: async () => structuredClone(audition) };
      }
      assert.equal(allowProjectDraft, true, `Bootstrap and switching must never submit: ${path}`);
      assert.equal(options.method, 'POST');
      assert.equal(path, '/api/projects/audition/motion-renders');
      writes.push({ path, body: JSON.parse(options.body) });
      return { ok: true, status: 202, json: async () => ({ id: 'whole-project-draft', purpose: 'draft', status: 'queued' }) };
    }
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
    await t.test('the real whole-project draft button submits all adopted scenes without verifying estimated timing', async () => {
      await clickProject(audition.id); healthy(audition.id);
      assert.equal(audition.scenes.length, 2, 'This action must not become a selected-scene draft');
      assert.equal($('preview-batch-project').disabled, false);
      assert.match($('motion-timing-status').textContent, /待人工核对|估算/);
      assert.equal($('confirm-motion-timing').checked, false);
      assert.equal(writes.length, 0, 'Reading candidates and estimated timing must remain read-only');
      $('render-end-hold').checked = true;
      allowProjectDraft = true;
      await $('preview-batch-project').emit('click');
      await nextTurn();
      allowProjectDraft = false;
      assert.deepEqual(writes, [{ path: '/api/projects/audition/motion-renders',
        body: { expected_revision: 1, purpose: 'draft', subtitles: false, end_hold_seconds: 4 } }]);
      assert.equal($('global-error').hidden, true, $('error-text').textContent);
      assert.match($('success-message').textContent, /整片草稿制作已开始/);
      assert.match($('motion-timing-status').textContent, /待人工核对|估算/,
        'Rendering a draft must not mark its recording-bound timing as verified');
      assert.equal($('confirm-motion-timing').checked, false);
    });
    await t.test('completed narration has an explicit exact-result audition without adopting or replacing playback on refresh', async () => {
      await clickProject(audition.id);
      const audio = $('audio-preview'), button = $('audition-operation-audio');
      const originalMotion = structuredClone(auditionScene.motion);
      const originalAudio = structuredClone(auditionAudio);
      await audio.play(); audio.currentTime = 1.25;
      $('project-speech-voice').value = 'other-voice'; await $('project-speech-voice').emit('change');
      assert.equal($('generate-audio').disabled, true, 'An unsaved project voice still blocks synthesis');
      allowVoiceSave = true;
      await $('save-project-speech').emit('click'); await nextTurn();
      allowVoiceSave = false;
      assert.deepEqual(voiceWrites, [{ expected_revision: 1, voice: 'other-voice' }]);
      assert.match($('success-message').textContent, /有效的动态图解可继续使用.*再核对画面时序/);
      assert.equal($('audio-version-select').value, auditionAudio.id);
      assert.equal(audio.paused, false); assert.equal(audio.currentTime, 1.25);
      const completed = { id: 'voice-change-op', kind: 'speech', status: 'completed', candidate_id: 'changed-audio' };
      const changed = { ...auditionAudio, id: completed.candidate_id, number: 2,
        operation_id: completed.id, voice: 'other-voice', stale: false };
      const unrelated = { ...changed, id: 'unrelated-newer-audio', number: 3,
        operation_id: 'another-op', source: 'upload' };
      auditionScene.audio_versions.push(changed, unrelated);
      auditionScene.audio_operation = completed; audition.revision++;
      await $('refresh-all').emit('click');
      assert.equal($('audio-version-select').value, auditionAudio.id, 'Completion preserves the historical audition');
      assert.equal(audio.src, '/api/audio-versions/audition-audio/audio');
      assert.equal(audio.paused, false); assert.equal(audio.currentTime, 1.25);
      assert.equal(button.hidden, false); assert.equal(button.disabled, false);
      assert.equal(button.textContent, '试听本次配音 A2 · 另一音色');
      assert.match($('audio-status-text').textContent, /当前试听版本保持不变/);
      assert.doesNotMatch(button.textContent, /最新|A3/);
      await $('motion-timing-audio').play();
      await button.emit('click');
      assert.equal($('audio-version-select').value, changed.id);
      assert.equal(audio.src, '/api/audio-versions/changed-audio/audio');
      assert.equal(audio.paused, false); assert.equal(audio.currentTime, 0);
      assert.equal($('motion-timing-audio').paused, true, 'The existing media mutex handles explicit audition');
      assert.equal(button.hidden, true);
      assert.match($('audio-version-summary').textContent, /^A2 · 另一音色/);
      assert.match($('audio-status-text').textContent, /已选为试听版本/);
      assert.equal(auditionScene.head_audio_version_id, auditionAudio.id, 'Audition is never adoption');
      assert.deepEqual(auditionScene.motion, originalMotion, 'Existing diagram versions and adoption stay intact');
      assert.deepEqual(auditionAudio, { ...originalAudio, stale: true }, 'The original recording identity stays intact');
      assert.equal($('confirm-motion-timing').checked, false);
      assert.equal(writes.length, 1, 'Only the earlier explicit draft POST exists; audition issues no POST');

      $('audio-version-select').value = auditionAudio.id; await $('audio-version-select').emit('change');
      $('project-speech-voice').value = 'test-voice'; await $('project-speech-voice').emit('change');
      await $('refresh-all').emit('click');
      assert.equal($('project-speech-voice').value, 'test-voice', 'Refresh preserves the unsaved voice');
      assert.equal($('generate-audio').disabled, true);
      assert.match($('audio-status-text').textContent, /音色尚未保存.*已保存的设置/);
      await button.emit('click');
      assert.equal($('project-speech-voice').value, 'test-voice', 'Audition never saves a voice draft');
      assert.equal(audition.speech_settings.voice, 'other-voice');
      assert.equal(voiceWrites.length, 1); assert.equal(writes.length, 1);
    });
    await t.test('invalid, stale and other-scene operation results never expose or activate the completion audition', async () => {
      const button = $('audition-operation-audio'), audio = $('audio-preview');
      const completed = structuredClone(auditionScene.audio_operation);
      const changed = auditionScene.audio_versions.find(row => row.id === completed.candidate_id);
      $('audio-version-select').value = auditionAudio.id; await $('audio-version-select').emit('change');
      const currentSource = audio.src;
      for (const operation of [null,
        ...['running', 'failed', 'cancelled', 'timed_out'].map(status => ({ ...completed, status })),
        { ...completed, candidate_id: null }, { ...completed, candidate_id: 'missing' },
        { ...completed, candidate_id: secondDraftScene.audio_versions[0].id },
        { ...completed, id: 'mismatched-operation' },
        { ...completed, kind: 'unrecognized' },
      ]) {
        auditionScene.audio_operation = operation; audition.revision++;
        await $('refresh-all').emit('click');
        assert.equal(button.hidden, true, `No shortcut for ${JSON.stringify(operation)}`);
        assert.equal(button.disabled, true);
        await button.emit('click'); assert.equal(audio.src, currentSource);
      }
      auditionScene.audio_operation = completed; changed.stale = true; audition.revision++;
      await $('refresh-all').emit('click');
      assert.equal(button.hidden, true); assert.match($('audio-status-text').textContent, /本次配音 A2 · 另一音色.*需更新/);
      await button.emit('click'); assert.equal($('audio-version-select').value, auditionAudio.id);
      assert.equal($('adopt-audio').disabled, true, 'The preserved stale audition remains ineligible for adoption');
      changed.stale = false; audition.revision++;
      await $('refresh-all').emit('click'); assert.equal(button.hidden, false);
      auditionScene.audio_operation = { id: 'another-op', kind: 'audio_upload', status: 'completed',
        candidate_id: 'unrelated-newer-audio' }; audition.revision++;
      await $('refresh-all').emit('click');
      assert.equal(button.textContent, '试听本次配音 A3 · 上传录音', 'A later operation resolves its own exact recording without inferring a voice');
      assert.match($('audio-status-text').textContent, /^上传配音/);
      await button.emit('click'); assert.equal(audio.src, '/api/audio-versions/unrelated-newer-audio/audio');
      assert.equal(auditionScene.head_audio_version_id, auditionAudio.id);
      $('audio-version-select').value = auditionAudio.id; await $('audio-version-select').emit('change');
      auditionScene.audio_operation = completed; audition.revision++;
      await $('refresh-all').emit('click');
      const play = audio.play;
      audio.play = async () => { throw new Error('Offline autoplay rejection'); };
      await button.emit('click'); audio.play = play;
      assert.equal(audio.src, '/api/audio-versions/changed-audio/audio');
      assert.match($('success-message').textContent, /已切换到配音 A2.*点击播放/);
      assert.equal($('global-error').hidden, true);
      $('audio-version-select').value = auditionAudio.id; await $('audio-version-select').emit('change');
      const secondSceneButton = $('scene-list').children.find(row => row.dataset.sceneId === secondDraftScene.id);
      await secondSceneButton.emit('click'); await nextTurn();
      assert.equal(button.hidden, true); assert.equal(audio.paused, true);
      assert.equal(audio.src, '/api/audio-versions/audition-second-audio/audio');
      assert.equal($('project-speech-voice').value, 'test-voice', 'Scene switches preserve the unsaved project voice');
      const firstSceneButton = $('scene-list').children.find(row => row.dataset.sceneId === auditionScene.id);
      await firstSceneButton.emit('click'); await nextTurn();
      assert.equal(button.hidden, false); assert.equal($('audio-version-select').value, auditionAudio.id);
      await clickProject(idle.id);
      assert.equal(button.hidden, true); await button.emit('click');
      assert.equal(audio.src, undefined); assert.equal(writes.length, 1); assert.equal(voiceWrites.length, 1);
    });
    await t.test('saving a dirty scene submits its original editable base after an unrelated refresh', async () => {
      await clickProject(audition.id);
      const originalNarration = auditionScene.narration, originalRevision = audition.revision;
      await $('toggle-scene-editor').emit('click');
      $('scene-narration').value = '这段旁白正在编辑。';
      await $('scene-form').emit('input');
      audition.scenes[1].title = '另一镜头更新'; audition.revision++;
      await $('refresh-all').emit('click');
      assert.equal($('scene-narration').value, '这段旁白正在编辑。');
      $('global-error').hidden = false; $('error-text').textContent = '旧的错误提示';
      allowSceneSave = true;
      await $('scene-form').emit('submit'); allowSceneSave = false;
      assert.equal(sceneWrites.length, 1);
      assert.equal(sceneWrites[0].expected_revision, originalRevision);
      assert.equal(sceneWrites[0].base_fields.narration, originalNarration);
      assert.equal(sceneWrites[0].narration, '这段旁白正在编辑。');
      assert.equal(sceneWrites[0].dirty, undefined);
      assert.equal($('global-error').hidden, true);
    });
    for (const [id, timer] of timers) if (timer.duration === 5000) {
      timers.delete(id); timer.callback();
    }
    await windowEvents.emit('pagehide');
    assert.equal(timers.size, 0, 'Project switch and page teardown clear polling');
  } finally {
    for (const [key, descriptor] of Object.entries(originals)) {
      if (descriptor) Object.defineProperty(globalThis, key, descriptor); else delete globalThis[key];
    }
  }
});
