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
  focus() {}
  reset() {}
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
  const batchBusy = project('batch-busy'), jointBusy = project('joint-busy'), historical = project('historical', 'static');
  const audition = project('audition');
  const unmade = project('unmade'); unmade.scenes = [];
  const auditionScene = audition.scenes[0];
  const auditionAudio = { id: 'audition-audio', number: 1, sha256: 'a'.repeat(64), duration_seconds: 4,
    voice: 'test-voice', source: 'tts', operation_id: 'original-audio-op', stale: false };
  auditionScene.audio_versions = [auditionAudio]; auditionScene.head_audio_version_id = auditionAudio.id;
  auditionScene.motion.versions = [{ id: 'audition-motion', number: 1, plan_sha256: 'b'.repeat(64) }];
  const auditionVersion = { id: 'audition-motion', project_id: audition.id, scene_id: auditionScene.id, plan: { template: 'flow', title: '逐段试听',
    takeaway: '每段单独核对', elements: [{ id: 'one', label: '第一步', detail: '先说明问题' }],
    relations: [], actions: [{ target: 'one', kind: 'move', beat_id: 'first', placement: 'start', pace: 'normal' }] }, beats: { segments: [
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
  const productionBusy = project('production-busy'); productionBusy.joint_plan = {version_id:'adopted-plan'};
  const removable = project('removable');
  const drafted = project('drafted'); drafted.joint_plan = {version_id:'drafted-plan'};
  const draftedSecond = structuredClone(drafted.scenes[0]); draftedSecond.id = 'drafted-second'; drafted.scenes.push(draftedSecond);
  const all = [idle, motionBusy, explanationBusy, jointBusy, batchBusy, productionBusy, historical, audition, unmade, removable, drafted];
  const deletedProjects = new Set(), lifecycleWrites = [];
  let listedProjects = [];
  const batch = { id: 'batch-op', project_id: batchBusy.id, status: 'running', items: [] };
  const settings = { user_id: 'offline-bootstrap-test', csrf_token: 'offline-only',
    motion_enabled: true, motion_configured: true, motion_render_configured: true,
    batch_enabled: true, explanation_configured: true, joint_plan_configured: true, production_runs_enabled:true, speech_configured: true,
    speech_voice: 'test-voice', speech_voices: [{ id: 'test-voice', name: '测试音色', default: true },
      { id: 'other-voice', name: '另一音色' }] };
  const writes = [], voiceWrites = [], sceneWrites = [], mutationCalls = [];
  const storyboardWrites = [];
  const renderRows = new Map();
  const productionRunRows = new Map();
  let allowProjectDraft = false, allowVoiceSave = false, allowSceneSave = false, draftFailure = null;
  let allowProjectCreate = false;
  const fetchFixture = async (path, options) => {
    calls.push(path);
    if (options.method !== 'GET') {
      mutationCalls.push({ path, method: options.method });
      if (path === '/api/projects' && options.method === 'POST') {
        assert.equal(allowProjectCreate, true);
        const body = JSON.parse(options.body), created = project('newly-created');
        Object.assign(created, body, { scenes: [] });
        all.push(created);
        if (listedProjects !== all) listedProjects.push(created);
        return { ok: true, status: 201, json: async () => structuredClone(created) };
      }
      const managedProject = all.find(p => path === `/api/projects/${p.id}/trash` || path === `/api/projects/${p.id}/restore`);
      if (managedProject && ['removable', 'newly-created'].includes(managedProject.id)) {
        lifecycleWrites.push({ path, body: JSON.parse(options.body) });
        assert.equal(JSON.parse(options.body).expected_revision, managedProject.revision);
        if (path.endsWith('/trash')) deletedProjects.add(managedProject.id); else deletedProjects.delete(managedProject.id);
        managedProject.revision++;
        return { ok: true, status: 200, json: async () => ({ project: {
          id: managedProject.id, title: managedProject.title, revision: managedProject.revision,
          deleted_at: deletedProjects.has(managedProject.id) ? 123 : null,
        } }) };
      }
      if (path === '/api/projects/unmade/storyboard') {
        storyboardWrites.push(JSON.parse(options.body)); unmade.revision++;
        return { ok: false, status: 409, json: async () => ({ detail: { code: 'revision_conflict' } }) };
      }
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
      if (draftFailure === 'timing-review') {
        assert.equal(writes.at(-1).body.purpose, 'delivery');
        return { ok: false, status: 409, json: async () => ({ detail: { code: 'motion_timing_needs_review' } }) };
      }
      if (draftFailure === '409') {
        audition.revision++;
        return { ok: false, status: 409, json: async () => ({ detail: { code: 'revision_conflict' } }) };
      }
      if (draftFailure === 'network') {
        renderRows.set(audition.id, [{ id: 'accepted-before-disconnect', status: 'running', purpose: 'draft', created_at: Date.now() / 1000 }]);
        throw new TypeError('fetch failed after admission');
      }
      return { ok: true, status: 202, json: async () => ({ id: 'whole-project-draft', purpose: 'draft', status: 'queued' }) };
    }
    let result;
    if (path === '/api/session') result = settings;
    else if (path === '/api/projects') result = { projects: listedProjects.filter(p => !deletedProjects.has(p.id))
      .map(p => ({ id: p.id, title: p.title, revision: p.revision, scene_count: p.scenes.length })) };
    else if (path === '/api/projects?deleted=true') result = { projects: listedProjects.filter(p => deletedProjects.has(p.id))
      .map(p => ({ id: p.id, title: p.title, revision: p.revision, scene_count: p.scenes.length, deleted_at: 123 })) };
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
      else if (suffix === 'renders') result = { renders: renderRows.get(id) ?? [] };
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
      else if (suffix === 'joint-plan') result = { versions: [],
        operation: id === jointBusy.id ? { id: 'joint-op', status: 'running' } : null };
      else if (suffix === 'batches') result = { batches: id === batchBusy.id ? [batch] : [] };
      else if (suffix === 'production-runs') result = {runs:productionRunRows.get(id) ?? (id === productionBusy.id ? [{id:'whole-video-run',project_id:id,
        joint_plan_version_id:'adopted-plan',request_key:'production-run:existing',voice:'test-voice',status:'running',phase:'audio',items:[]}] : [])};
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
      window: { addEventListener: (...args) => windowEvents.addEventListener(...args), confirm: () => true },
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
    assert.equal($('generate-explanation').disabled, true, 'Previous planner remains available for historical review only');
    assert.equal($('generate-joint-plan').disabled, false);
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

    await clickProject(jointBusy.id);
    healthy(jointBusy.id);
    assert.equal($('generate-joint-plan').disabled, true);
    assert.equal($('generate-motion').disabled, true, 'Whole-video planning blocks competing scene work');
    assert.equal($('preview-batch').disabled, true);
    assert.equal($('convert-project-motion').disabled, true);

    await clickProject(productionBusy.id);
    healthy(productionBusy.id);
    for (const id of ['start-production-run','generate-joint-plan','adopt-joint-plan','generate-motion','preview-batch',
      'project-speech-voice','save-project-speech','add-scene','toggle-scene-editor','scene-narration','generate-audio']) {
      assert.equal($(id).disabled,true,`Whole-video run must fence ${id}`);
    }
    assert.match($('production-run-status').textContent,/制作中/);
    assert.equal($('cancel-production-run').disabled,false);
    const beforeBusyWrites=mutationCalls.length;
    await $('create-storyboard').emit('click'); await $('generate-audio').emit('click');
    assert.equal(mutationCalls.length,beforeBusyWrites,'Busy protection must also fence programmatic actions');

    await clickProject(batchBusy.id);
    healthy(batchBusy.id);
    await $('toggle-scene-editor').emit('click');
    assert.equal($('save-scene').disabled, true);
    assert.match($('scene-edit-note').textContent, /批量制作.*暂时不能保存.*批次结束或取消完成/);
    $('scene-visual').value = '批次运行期间先整理的画面草稿';
    await $('scene-form').emit('input');
    assert.match($('draft-status').textContent, /草稿未保存.*等待批次结束/);
    await $('cancel-scene-edit').emit('click');
    assert.match($('success-message').textContent, /未保存的草稿已保留/);
    for (const [id, timer] of timers) if (timer.duration === 5000) {
      timers.delete(id); timer.callback();
    }
    await $('toggle-scene-editor').emit('click');
    assert.equal($('scene-visual').value, '批次运行期间先整理的画面草稿');
    batch.status = 'completed';
    await $('refresh-all').emit('click');
    assert.equal($('save-scene').disabled, false);
    assert.match($('scene-edit-note').textContent, /收起编辑会保留.*重新载入会放弃/);
    batch.status = 'running';
    await $('refresh-all').emit('click');
    assert.equal($('generate-audio').disabled, true);
    assert.equal($('generate-explanation').disabled, true);
    assert.equal($('generate-joint-plan').disabled, true);
    assert.equal($('generate-motion').disabled, true);
    assert.equal($('preview-batch').disabled, true);

    await clickProject(historical.id);
    healthy(historical.id);
    assert.equal($('batch-panel').hidden, true);
    assert.equal($('generate-joint-plan').disabled, false, 'Previous project activity cannot leak into the new project');
    assert.equal($('convert-project-motion').disabled, false);
    motionBusy.scenes[0].motion.operation.status = 'uncertain';
    await clickProject(motionBusy.id);
    healthy(motionBusy.id);
    assert.equal($('convert-project-motion').disabled, true, 'An uncertain operation remains unresolved');
    assert.equal($('preview-batch').disabled, true);
    assert.ok([...timers.values()].some(timer => timer.duration === 15000), 'Uncertain work keeps a slow read-only reconciliation loop');
    assert.ok(![...timers.values()].some(timer => timer.duration === 3500), 'Uncertain work is not treated as a running operation');
    await clickProject(idle.id);
    healthy(idle.id);
    assert.equal($('generate-motion').disabled, false);
    assert.equal($('preview-batch').disabled, false);
    await $('refresh-all').emit('click');
    healthy(idle.id);
    assert.ok(calls.includes('/api/projects/batch-busy/batches'));
    assert.ok(calls.includes('/api/projects/explanation-busy/explanation'));
    assert.ok(calls.includes('/api/projects/joint-busy/joint-plan'));
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
      assert.equal(mediaIds.length, 6, 'Cover every native audio and video player in the real page');
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
    await t.test('whole-project 409 and lost admission responses reconcile with GETs and never automatically repeat POST', async () => {
      await clickProject(audition.id); await $('reset-project-speech').emit('click');
      for (const scene of audition.scenes) {
        scene.audio_stale = false; scene.audio_versions.forEach(audio => { audio.stale = false; });
      }
      await $('refresh-all').emit('click');
      assert.equal($('preview-batch-project').disabled, false);
      const before = writes.length, previousRevision = audition.revision, beforeReads = calls.length;
      allowProjectDraft = true; draftFailure = '409';
      await $('preview-batch-project').emit('click'); await nextTurn();
      assert.equal(writes.length, before + 1);
      assert.equal(writes.at(-1).body.expected_revision, previousRevision);
      assert.ok(calls.slice(beforeReads).includes('/api/projects/audition'));
      assert.ok(calls.slice(beforeReads).includes('/api/projects/audition/renders'));
      assert.equal($('global-error').hidden, false);
      assert.match($('error-text').textContent, /已有更新.*所选范围会保留/);
      assert.equal($('delivery-panel').open, true);
      assert.equal($('delivery-error').hidden, false);
      assert.match($('delivery-error-text').textContent, /已有更新/);
      await $('reload-delivery').emit('click');
      assert.equal(writes.length, before + 1, 'The local output recovery button only reads');
      assert.equal($('delivery-error').hidden, false, 'A GET does not silently dismiss the write failure');
      assert.equal($('preview-batch-project').disabled, false, 'Failed submit releases its pending lock after reading the latest revision');
      draftFailure = 'network';
      await $('preview-batch-project').emit('click'); await nextTurn();
      assert.equal(writes.length, before + 2);
      assert.equal($('global-error').hidden, false);
      assert.match($('error-text').textContent, /结果尚未确认.*不会自动重复提交/);
      assert.match($('render-history').textContent, /制作中/);
      assert.equal($('preview-batch-project').disabled, true, 'A recovered in-flight render blocks a duplicate submission');
      allowProjectDraft = false; draftFailure = null; renderRows.delete(audition.id);
      await $('refresh-all').emit('click');
    });
    await t.test('delivery timing review rejection explains the manual gate without verifying or retrying the export', async () => {
      await clickProject(audition.id);
      assert.equal($('render-motion-project').disabled, false);
      assert.equal($('confirm-motion-timing').checked, false);
      const beforeWrites = writes.length, beforeMutations = mutationCalls.length, previousRevision = audition.revision;
      allowProjectDraft = true; draftFailure = 'timing-review';
      await $('render-motion-project').emit('click'); await nextTurn();
      allowProjectDraft = false; draftFailure = null;
      assert.equal(writes.length, beforeWrites + 1);
      assert.equal(writes.at(-1).body.purpose, 'delivery');
      assert.equal(writes.at(-1).body.expected_revision, previousRevision);
      const message = '动态时序仍待人工试听核对，完成后才能正式导出。';
      assert.equal($('global-error').hidden, false);
      assert.equal($('error-text').textContent, message);
      assert.equal($('delivery-error').hidden, false);
      assert.equal($('delivery-error-text').textContent, message);
      await $('reload-delivery').emit('click'); await $('refresh-all').emit('click'); await nextTurn();
      assert.equal($('delivery-error-text').textContent, message, 'Read-only reconciliation keeps the actionable review message');
      assert.deepEqual(mutationCalls.slice(beforeMutations), [
        { path: '/api/projects/audition/motion-renders', method: 'POST' },
      ], 'A rejected delivery neither retries the export nor submits timing verification');
      assert.equal($('confirm-motion-timing').checked, false);
      assert.match($('motion-timing-status').textContent, /尚未人工核对/);
      assert.equal(audition.revision, previousRevision);
    });
    await t.test('a failed storyboard start has a persistent local error and a read-only recovery button', async () => {
      await clickProject(unmade.id);
      $('storyboard-max-scenes').valueAsNumber = 6;
      await $('create-storyboard').emit('click'); await nextTurn();
      assert.equal(storyboardWrites.length, 1);
      assert.equal(storyboardWrites[0].max_scenes, 6);
      assert.equal($('storyboard-status').hidden, false);
      assert.match($('storyboard-status-text').textContent, /已有更新/);
      assert.equal($('reload-storyboard').hidden, false);
      assert.equal($('cancel-storyboard').hidden, true);
      await $('reload-storyboard').emit('click');
      assert.equal(storyboardWrites.length, 1);
      assert.equal($('storyboard-status').hidden, false);
      assert.equal($('storyboard-max-scenes').valueAsNumber, 6);
    });
    await t.test('render failures retrieve and highlight the exact frozen candidate instead of the current adopted version', async () => {
      const beforeWrites = writes.length;
      renderRows.set(audition.id, [{ id: 'failed-old-render', status: 'failed', purpose: 'draft', error: 'motion_invisible_move',
        scenes: [{ scene_id: auditionScene.id, visual_mode: 'motion', visual_version_id: auditionVersion.id }],
        diagnostic: { scene_index: 0, motion_context: { target: 'one', kind: 'move', ordinal: 0, beat_id: 'first' } } }]);
      const before = calls.length;
      await clickProject(audition.id);
      assert.ok(calls.slice(before).includes('/api/motion-versions/audition-motion'));
      assert.match($('render-history').textContent, /第一步（one）.*移动/);
      const walk = row => [row, ...row.children.flatMap(walk)];
      const locate = walk($('render-history')).find(row => row.tagName === 'button' && row.textContent === '查看问题图元与动作');
      assert.ok(locate); await locate.emit('click');
      assert.equal($('motion-version').value, auditionVersion.id);
      assert.ok(walk($('motion-review')).some(row => row.className === 'motion-diagnostic-target'));
      assert.equal(writes.length, beforeWrites);
      renderRows.delete(audition.id);
    });
    await t.test('wall-clock progress advances without network and visibility, focus and pageshow reconcile every panel once', async () => {
      const originalNow = Date.now; let now = originalNow(); Date.now = () => now;
      const beforeWrites = writes.length;
      try {
        batch.status = 'running';
        batch.created_at = now / 1000 - 60;
        const render = { id: 'background-render', status: 'running', created_at: now / 1000 - 60,
          progress: { stage: 'rendering_scene', scene_index: 0, completed_scenes: 0, total_scenes: 1 } };
        renderRows.set(batchBusy.id, [render]);
        await clickProject(batchBusy.id);
        assert.match($('render-history').textContent, /已等待 1分0秒/);
        assert.match($('batch-status').textContent, /已等待 1分0秒/);
        const beforeTick = calls.length;
        now += 125000;
        const clock = [...timers.entries()].find(([, timer]) => timer.duration === 1000);
        assert.ok(clock); timers.delete(clock[0]); clock[1].callback();
        assert.match($('render-history').textContent, /已等待 3分5秒/);
        assert.match($('batch-status').textContent, /已等待 3分5秒/);
        assert.equal(calls.length, beforeTick, 'Clock tick redraws time without polling the server');
        document.hidden = true; await pageEvents.emit('visibilitychange');
        assert.ok(![...timers.values()].some(timer => [1000, 3500, 15000].includes(timer.duration)));
        batch.status = 'completed'; render.status = 'completed'; now += 40000;
        document.hidden = false;
        const beforeResume = calls.length;
        await Promise.all([pageEvents.emit('visibilitychange'), windowEvents.emit('focus'), windowEvents.emit('pageshow')]);
        const resumed = calls.slice(beforeResume);
        for (const suffix of ['', '/renders', '/motion', '/explanation', '/joint-plan', '/batches', '/continuity']) {
          // Continuity is feature-gated in this fixture; the enabled panels must all catch up.
          if (suffix === '/continuity') continue;
          assert.equal(resumed.filter(path => path === `/api/projects/batch-busy${suffix}`).length, 1, suffix);
        }
        assert.match($('batch-status').textContent, /已完成/);
        assert.match($('render-history').textContent, /已完成/);
        assert.doesNotMatch($('render-history').textContent, /申请取消|已等待/);
        assert.equal($('cancel-batch').hidden, true);
        batch.status = 'running';
        const beforeFocus = calls.length; await windowEvents.emit('focus');
        assert.ok(calls.length > beforeFocus, 'Focus alone also catches up when visibility did not fire');
        assert.match($('batch-status').textContent, /制作中/);
        batch.status = 'completed'; await windowEvents.emit('pageshow');
        assert.equal($('cancel-batch').hidden, true);
        assert.equal(writes.length, beforeWrites, 'All recovery events remain read-only');
      } finally { Date.now = originalNow; renderRows.delete(batchBusy.id); }
    });
    await t.test('whole-video candidates are distinct from adoption, show measured duration, and never follow the history selector', async () => {
      const previousMutations = mutationCalls.length;
      const current = {id:'drafted-run',project_id:drafted.id,joint_plan_version_id:'drafted-plan',voice:'test-voice',
        status:'completed',phase:'completed',current:true,created_at:2,draft_render_id:'drafted-render',duration_seconds:24,
        items:drafted.scenes.map((scene,index)=>({scene_id:scene.id,title:scene.title,audio_seconds:index ? 10 : 13,
          duration_seconds:index ? 10.5 : 13.5,audio_version_id:`audio-${index}`,motion_version_id:`motion-${index}`,
          candidate_current:true,status:'completed'}))};
      const old={...structuredClone(current),id:'older-run',created_at:1,current:false,draft_render_id:'older-render'};
      productionRunRows.set(drafted.id,[current,old]);
      renderRows.set(drafted.id,[{id:'drafted-render',status:'completed',selection:'draft_only',purpose:'draft',
        content_state:'current',actual_duration_seconds:24.5},
        {id:'older-render',status:'completed',selection:'draft_only',purpose:'draft',content_state:'changed',actual_duration_seconds:20}]);
      await clickProject(drafted.id);
      for(const card of $('scene-list').children) {
        assert.match(card.textContent,/草稿画面就绪.*草稿配音就绪/s);
        assert.match(card.textContent,/人工采用：画面未采用.*配音未采用/s);
        assert.doesNotMatch(card.textContent,/动态图解待制作/);
      }
      assert.match($('project-meta').textContent,/整片草稿实际 24.5 秒/);
      assert.match($('project-duration-summary').textContent,/视频实际 24.5 秒.*全片录音 23 秒.*排程 24 秒/s);
      assert.equal($('production-run-status').textContent.match(/草稿待审阅/g)?.length,1);
      assert.equal($('render-history').children.length,0,'The same completed run render is not repeated in legacy output history');
      current.items[1].candidate_current=false;
      await $('refresh-all').emit('click');
      assert.match($('scene-list').children[0].textContent,/草稿画面就绪/);
      assert.match($('scene-list').children[1].textContent,/草稿画面待确认.*草稿配音待确认/s);
      assert.doesNotMatch($('project-meta').textContent,/整片草稿实际/,'An unverified pair cannot establish current whole-video duration');
      current.items[1].candidate_current=true;
      await $('refresh-all').emit('click');
      $('production-run-select').value=old.id;await $('production-run-select').emit('change');
      assert.equal($('production-run-video').src,'/api/renders/older-render/video');
      assert.match($('production-run-review-note').textContent,/项目内容已变化/);
      assert.match($('project-meta').textContent,/整片草稿实际 24.5 秒/,'Reviewing history does not replace current project duration');
      assert.ok($('scene-list').children.every(card=>/草稿画面就绪/.test(card.textContent)));
      current.current=false;
      await $('refresh-all').emit('click');
      assert.ok($('scene-list').children.every(card=>!/草稿画面就绪|草稿配音就绪/.test(card.textContent)));
      assert.doesNotMatch($('project-meta').textContent,/整片草稿实际/);
      current.current=true;
      productionRunRows.set(drafted.id,[{...structuredClone(current),id:'failed-latest',created_at:3,status:'failed',error:'child_failed'},current,old]);
      await $('refresh-all').emit('click');
      assert.ok($('scene-list').children.every(card=>!/草稿画面就绪/.test(card.textContent)),
        'A newer failed attempt cannot project older completed candidates as this attempt');
      await clickProject(idle.id);
      assert.equal($('production-run-player').hidden,true);
      assert.equal($('production-run-video').src,undefined);
      assert.equal(mutationCalls.length,previousMutations);
      for(const scene of drafted.scenes) {
        assert.equal(scene.head_audio_version_id,null);assert.equal(scene.motion.head_version_id,null);
      }
      productionRunRows.delete(drafted.id); renderRows.delete(drafted.id);
    });
    await t.test('deleting the selected project stops its players and restores the same project without generation', async () => {
      await clickProject(removable.id);
      for (const id of ['audio-preview', 'motion-timing-audio', 'batch-audio-preview',
        'project-player-audio', 'motion-render-video', 'production-run-video']) await $(id).play();
      assert.equal($('delete-project').disabled, false);
      const previousMutations = mutationCalls.length;
      await $('delete-project').emit('click');
      assert.equal($('delete-project-dialog').open, true);
      assert.equal(mutationCalls.length, previousMutations, 'Opening a confirmation does not delete');
      await $('confirm-delete-project').emit('click');
      assert.equal(lifecycleWrites.length, 1);
      assert.equal($('delete-project-dialog').open, false);
      assert.equal($('project-title').textContent, idle.title);
      assert.ok(!$('project-list').children.some(row => row.dataset.projectId === removable.id));
      for (const id of ['audio-preview', 'motion-timing-audio', 'batch-audio-preview',
        'project-player-audio', 'motion-render-video', 'production-run-video']) assert.equal($(id).paused, true, id);
      await $('show-project-trash').emit('click');
      assert.equal($('project-count').textContent, '1');
      for (const [prefix, projectId] of [['joint-plan', unmade.id], ['explanation', historical.id]]) {
        await $('show-active-projects').emit('click'); await clickProject(projectId);
        for (const field of ['audience', 'goal', 'instruction']) {
          $(`${prefix}-${field}`).value = `尚未提交的${field}`;
          await $(`${prefix}-${field}`).emit('input'); await $('workspace').emit('input');
          assert.equal($('delete-project').disabled, true, `${prefix} ${field} must fence deletion`);
          await $('delete-project').emit('click');
          assert.equal(lifecycleWrites.length, 1);
          assert.match($('error-text').textContent, /保存或撤销/);
          await $('show-project-trash').emit('click');
          await $('project-list').children[0].children[1].emit('click');
          assert.equal(lifecycleWrites.length, 1, 'Restore-and-open must not lose an unsubmitted brief');
          assert.match($('error-text').textContent, /保存或撤销/);
          await $('show-active-projects').emit('click');
        }
      }
      await clickProject(idle.id);
      assert.equal($('delete-project').disabled, false, 'Drafts on other projects do not block this project');
      await $('show-project-trash').emit('click');
      const restore = $('project-list').children[0].children[1];
      await restore.emit('click');
      assert.equal(lifecycleWrites.length, 2);
      assert.equal($('project-title').textContent, removable.title);
      assert.ok($('project-list').children.some(row => row.dataset.projectId === removable.id));
      assert.deepEqual(mutationCalls.slice(previousMutations), [
        { path: '/api/projects/removable/trash', method: 'POST' },
        { path: '/api/projects/removable/restore', method: 'POST' },
      ], 'Deletion and restore do not generate or adopt media');
    });
    await t.test('newly created empty projects release the creation lock before the first delete or planning action', async () => {
      const previousMutations = mutationCalls.length;
      await $('new-project').emit('click');
      assert.equal($('create-project-dialog').open, true);
      $('new-title').value = '新建后立即管理'; $('new-script').value = '一段尚未开始制作的文稿。'; $('new-style').value = '';
      allowProjectCreate = true;
      await $('create-project-form').emit('submit');
      allowProjectCreate = false;
      assert.equal($('create-project-dialog').open, false);
      assert.equal($('project-title').textContent, '新建后立即管理');
      assert.equal($('scene-count').textContent, '0 个镜头');
      assert.equal($('delete-project').disabled, false, 'The completed create-project intent must not leave deletion locked');
      assert.equal($('delete-project-note').hidden, true);
      assert.equal($('generate-joint-plan').disabled, false, 'Planning must also see the released creation lock');
      await $('delete-project').emit('click'); await $('confirm-delete-project').emit('click');
      assert.ok(!$('project-list').children.some(row => row.dataset.projectId === 'newly-created'));
      assert.deepEqual(mutationCalls.slice(previousMutations), [
        { path: '/api/projects', method: 'POST' },
        { path: '/api/projects/newly-created/trash', method: 'POST' },
      ], 'Create and delete must not require a manual refresh or trigger paid production');
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
