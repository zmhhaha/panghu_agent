import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import {
  acceptProjectRead, activeOperations, audioPath, chooseAudioVersion, chooseVersion,
  createCaptionDraft, createIntentKeys, createWorkspaceState, issueProjectRead, deliveryIssues,
  parsePronunciation, pronunciationText, selectProject, selectedAudioVersion,
  selectedVersion, timelinePosition, validateCaptionCues,
  speechVoices, speechVoiceName, projectSpeechVoice, syncProjectSpeechDraft, projectVoiceOptions,
  projectSpeechCanGenerate, audioVoiceLabel,
} from '../../video_workbench/web/model.mjs';

function fixture(revision = 1) {
  return { id: 'project', revision, scenes: [{ id: 'scene', title: '第一镜头',
    versions: [{ id: 'visual', number: 1 }], head_version_id: 'visual',
    audio_versions: [{ id: 'audio-1', number: 1, duration_seconds: 4 }],
    head_audio_version_id: null, leading_seconds: 1, trailing_seconds: 2,
  }] };
}

function open(state, project) {
  selectProject(state, project.id);
  acceptProjectRead(state, issueProjectRead(state), project);
}

const voiceSession = { speech_configured: true, speech_voice: 'male-default', speech_voices: [
  { id: 'male-default', name: '默认男声', language: 'zh-CN', gender: 'male', default: true },
  { id: 'male-other', name: '另一男声', language: 'zh-CN', gender: 'male', default: false },
] };

test('voice choices use only the supplied catalog without inventing unavailable defaults', () => {
  assert.deepEqual(speechVoices({}), []);
  assert.deepEqual(projectVoiceOptions('', {}), [{ value: '', label: '暂无可用音色', disabled: true }]);
  assert.equal(projectVoiceOptions('male-default', voiceSession)[0].label, '默认男声 · 默认');
  const generic = { speech_voices: [{ id: 'custom', name: '自定义声音' }, { id: 'custom', name: '重复' }, null] };
  assert.deepEqual(speechVoices(generic).map(item => item.id), ['custom']);
  assert.equal(speechVoiceName('removed-voice', voiceSession), 'removed-voice');
});

test('one saved project voice applies across scenes and uses the server default for older projects', () => {
  const project = fixture();
  project.scenes[0].speech_settings = { voice: 'ignored-scene-voice' };
  assert.equal(projectSpeechVoice(project, voiceSession), 'male-default');
  project.speech_settings = { voice: 'male-other' };
  assert.equal(projectSpeechVoice(project, voiceSession), 'male-other');
  assert.equal(syncProjectSpeechDraft(null, project, voiceSession).voice, 'male-other');
  assert.equal(project.scenes[0].head_audio_version_id, null);
});

test('project voice drafts preserve unsaved selection and original revision across polling and scene changes', () => {
  const project = { ...fixture(3), speech_settings: { voice: 'male-default' } };
  const draft = syncProjectSpeechDraft(null, project, voiceSession);
  Object.assign(draft, { voice: 'male-other', dirty: true });
  const newer = { ...project, revision: 8 };
  assert.equal(syncProjectSpeechDraft(draft, newer, voiceSession), draft);
  assert.equal(draft.expected_revision, 3);
  assert.equal(projectSpeechCanGenerate(newer, voiceSession, draft), false);
  const reloaded = syncProjectSpeechDraft(null, newer, voiceSession);
  assert.equal(reloaded.voice, 'male-default');
  assert.equal(reloaded.expected_revision, 8);
  assert.equal(projectSpeechCanGenerate(newer, voiceSession, reloaded), true);
  assert.equal(syncProjectSpeechDraft(draft, { ...newer, id: 'other-project' }, voiceSession).voice, 'male-default');
});

test('catalog withdrawal preserves selected project voice while preventing new synthesis', () => {
  const project = { ...fixture(), speech_settings: { voice: 'removed-voice' } };
  const draft = syncProjectSpeechDraft(null, project, voiceSession);
  const options = projectVoiceOptions(draft.voice, voiceSession);
  assert.equal(options[0].value, 'removed-voice');
  assert.equal(options[0].disabled, true);
  assert.equal(projectSpeechCanGenerate(project, voiceSession, draft), false);
  assert.equal(projectSpeechCanGenerate(project, { ...voiceSession, speech_voices: [] }, draft), false);
  assert.equal(projectSpeechCanGenerate({ ...project, speech_settings: { voice: 'male-default' } },
    { ...voiceSession, speech_configured: false }), false);
  assert.equal(project.scenes[0].audio_versions[0].id, 'audio-1');
});

test('historical audio labels retain the actual voice and never infer an uploaded recording voice', () => {
  const original = { voice: 'male-default', source: 'generate' };
  assert.equal(audioVoiceLabel(original, voiceSession), '默认男声');
  const project = { speech_settings: { voice: 'male-other' } };
  assert.equal(projectSpeechVoice(project, voiceSession), 'male-other');
  assert.equal(audioVoiceLabel(original, voiceSession), '默认男声');
  assert.equal(audioVoiceLabel({ ...original, source: 'upload' }, voiceSession), '上传录音');
  assert.equal(audioVoiceLabel({ voice: 'removed-voice', source: 'generate' }, voiceSession), 'removed-voice');
  assert.equal(audioVoiceLabel({}, voiceSession), '未记录音色');
});

test('audio selection is independent of visuals and completion never adopts audio', () => {
  const state = createWorkspaceState();
  open(state, fixture());
  assert.equal(selectedAudioVersion(state).id, 'audio-1');
  assert.equal(state.project.scenes[0].head_audio_version_id, null);
  assert.equal(selectedVersion(state).id, 'visual');
  assert.equal(chooseVersion(state, 'scene', 'audio-1'), false);
  assert.equal(chooseAudioVersion(state, 'scene', 'visual'), false);
  assert.equal(selectedAudioVersion(state).id, 'audio-1');
});

test('late audio completion and another adopted head preserve the audition selection', () => {
  const state = createWorkspaceState();
  open(state, fixture());
  chooseAudioVersion(state, 'scene', 'audio-1');
  const newer = fixture(2);
  newer.scenes[0].audio_versions.push({ id: 'audio-2', number: 2 });
  newer.scenes[0].head_audio_version_id = 'audio-2';
  acceptProjectRead(state, issueProjectRead(state), newer);
  assert.equal(selectedAudioVersion(state).id, 'audio-1');
  assert.equal(state.project.scenes[0].head_audio_version_id, 'audio-2');
  assert.equal(selectedVersion(state).id, 'visual');
});

test('switching projects clears audio selection and rejects earlier audio responses', () => {
  const state = createWorkspaceState();
  open(state, fixture());
  const ticket = issueProjectRead(state);
  selectProject(state, 'other');
  assert.equal(state.selectedAudioVersions.size, 0);
  assert.equal(acceptProjectRead(state, ticket, fixture(8)), false);
  assert.equal(selectedAudioVersion(state), null);
});

test('polling includes audio tasks without changing visual task identity', () => {
  const data = fixture();
  data.scenes[0].operation = { id: 'visual-task', status: 'running' };
  data.scenes[0].audio_operation = { id: 'audio-task', status: 'cancelling' };
  assert.deepEqual(activeOperations(data).map(item => item.id), ['visual-task', 'audio-task']);
  data.scenes[0].audio_operation.status = 'cancelled';
  assert.deepEqual(activeOperations(data).map(item => item.id), ['visual-task']);
});

test('pronunciation edits preserve explicit word mappings and reject silently lost entries', () => {
  const value = parsePronunciation('SDK=艾斯迪开\n\nCordis = 科迪斯');
  assert.deepEqual(value, { SDK: '艾斯迪开', Cordis: '科迪斯' });
  assert.deepEqual(parsePronunciation(pronunciationText(value)), value);
  assert.deepEqual(parsePronunciation(`${'词'.repeat(80)}=${'音'.repeat(200)}`),
    { ['词'.repeat(80)]: '音'.repeat(200) });
  for (const input of ['SDK', '=读法', 'SDK=', 'SDK=读法\nSDK=另一读法',
    `${'词'.repeat(81)}=读法`, `词=${'音'.repeat(201)}`]) {
    assert.throws(() => parsePronunciation(input));
  }
  assert.throws(() => parsePronunciation(Array.from({ length: 33 }, (_, i) => `词${i}=读法`).join('\n')));
  const unusual = parsePronunciation('__proto__=原词');
  assert.equal(Object.getPrototypeOf(unusual), Object.prototype);
  assert.equal(Object.hasOwn(unusual, '__proto__'), true);
});

test('caption drafts copy cue values and bind audio, padding and original revision', () => {
  const scene = { ...fixture().scenes[0], head_audio_version_id: 'audio-1',
    captions: { cues: [{ start: 1, end: 3, text: '原字幕' }] } };
  const draft = createCaptionDraft(scene, 7);
  draft.cues[0].text = '我正在校正';
  assert.equal(scene.captions.cues[0].text, '原字幕');
  assert.equal(draft.audio_version_id, 'audio-1');
  assert.equal(draft.expected_revision, 7);
  assert.equal(draft.leading_seconds, 1);
  assert.equal(draft.trailing_seconds, 2);
});

test('caption validation rejects overlap, reversed time, missing text and out of scene bounds', () => {
  assert.equal(validateCaptionCues([{ start: 1, end: 3, text: '第一句' }, { start: 3, end: 5, text: '第二句' }], 6), true);
  for (const cues of [
    [{ start: -1, end: 2, text: '负数' }], [{ start: 3, end: 2, text: '反向' }],
    [{ start: 1, end: 6.2, text: '越界' }], [{ start: 1, end: 2, text: ' ' }],
    [{ start: 1, end: 3, text: '前' }, { start: 2, end: 4, text: '后' }],
    [{ start: NaN, end: 3, text: '无效时间' }],
  ]) assert.equal(validateCaptionCues(cues, 6), false);
  assert.equal(validateCaptionCues([{ start: 2, end: 6, text: '完整旁白' }], 9,
    { leadingSeconds: 2, audioSeconds: 4 }), true);
  assert.equal(validateCaptionCues([{ start: 1, end: 6, text: '落入留白' }], 9,
    { leadingSeconds: 2, audioSeconds: 4 }), false);
  assert.equal(validateCaptionCues([{ start: 2, end: 7, text: '超出旁白' }], 9,
    { leadingSeconds: 2, audioSeconds: 4 }), false);
  const maximum = Array.from({ length: 200 }, (_, i) => ({ start: i, end: i + 1, text: '一句' }));
  assert.equal(validateCaptionCues(maximum, 201), true);
  assert.equal(validateCaptionCues([...maximum, { start: 200, end: 201, text: '超额' }], 201), false);
  assert.equal(validateCaptionCues([], 201), false);
});

test('playback captions use scene time while speech offset excludes leading silence', () => {
  const timeline = { duration_seconds: 10, scenes: [
    { id: 'one', start_seconds: 0, duration_seconds: 8, leading_seconds: 2,
      audio_version_id: 'a1', audio_duration_seconds: 4,
      cues: [{ start: 2, end: 3, text: '第一句话' }] },
    { id: 'two', start_seconds: 8, duration_seconds: 2, audio_version_id: null },
  ] };
  assert.equal(timelinePosition(timeline, 1).speaking, false);
  const speaking = timelinePosition(timeline, 2.5);
  assert.equal(speaking.audioTime, 0.5);
  assert.equal(speaking.caption, '第一句话');
  assert.equal(speaking.speaking, true);
  assert.equal(timelinePosition(timeline, 6.5).speaking, false);
  assert.equal(timelinePosition(timeline, 8).scene.id, 'two');
  assert.equal(timelinePosition(timeline, 10), null);
  assert.equal(timelinePosition(timeline, -1), null);
  assert.equal(timelinePosition(timeline, Infinity), null);
});

test('stale audio never plays and immutable audio paths encode untrusted identifiers', () => {
  const timeline = { scenes: [{ id: 's', start_seconds: 0, duration_seconds: 8,
    audio_version_id: 'old', audio_duration_seconds: 4, audio_stale: true }] };
  assert.equal(timelinePosition(timeline, 1).speaking, false);
  assert.equal(audioPath('../other?a=1'), '/api/audio-versions/..%2Fother%3Fa%3D1/audio');
});

test('delivery issues identify scene order, titles and missing or stale adopted resources', () => {
  const project = { scenes: [
    { id: 'missing', title: '<原始标题>' },
    { id: 'old', title: '需更新', head_version_id: 'v', head_audio_version_id: 'a', audio_stale: true,
      captions: { audio_version_id: 'a', cues: [{ start: 0, end: 1, text: '旧字幕' }] } },
    { id: 'timing', title: '留白变化', head_version_id: 'v', head_audio_version_id: 'b',
      captions: { timing_stale: true, audio_version_id: 'b', cues: [{ start: 0, end: 1, text: '字幕' }] } },
    { id: 'ready', head_version_id: 'v', head_audio_version_id: 'c',
      captions: { requires_review: true, audio_version_id: 'c', cues: [{ start: 0, end: 1, text: '估算字幕' }] } },
  ] };
  assert.deepEqual(deliveryIssues(project), [
    { sceneId: 'missing', number: 1, title: '<原始标题>', reasons: ['missing_visual', 'missing_audio', 'missing_captions'] },
    { sceneId: 'old', number: 2, title: '需更新', reasons: ['stale_audio', 'stale_captions'] },
    { sceneId: 'timing', number: 3, title: '留白变化', reasons: ['stale_captions'] },
  ]);
  assert.deepEqual(deliveryIssues(project, { subtitles: false }).map(issue => issue.reasons),
    [['missing_visual', 'missing_audio'], ['stale_audio']]);
  assert.deepEqual(deliveryIssues(project, { silent: true }),
    [{ sceneId: 'missing', number: 1, title: '<原始标题>', reasons: ['missing_visual'] }]);
});

test('delivery issues follow audio adoption changes and distinguish empty captions from an old binding', () => {
  const scene = { id: 's', title: '镜头', head_version_id: 'v', head_audio_version_id: 'new',
    captions: { audio_version_id: 'old', cues: [{ start: 0, end: 1, text: '旧' }] } };
  assert.deepEqual(deliveryIssues({ scenes: [scene] })[0].reasons, ['stale_captions']);
  scene.captions = { cues: [] };
  assert.deepEqual(deliveryIssues({ scenes: [scene] })[0].reasons, ['missing_captions']);
  scene.captions = { audio_version_id: 'new', cues: [{ start: 0, end: 1, text: '新' }] };
  assert.deepEqual(deliveryIssues({ scenes: [scene] }), []);
  assert.deepEqual(deliveryIssues({ scenes: [] }), []);
});

test('uncertain raw uploads retain intent only for matching content and revision', () => {
  let id = 0;
  const keys = createIntentKeys(() => `upload-${++id}`);
  const intent = { audio_sha256: 'same-content', bytes: 44, expected_revision: 4 };
  const path = '/audio/upload?expected_revision=4';
  const key = keys.get('POST', path, intent);
  assert.equal(keys.get('POST', path, { ...intent }), key);
  assert.notEqual(keys.get('POST', path, { ...intent, audio_sha256: 'different' }), key);
  assert.notEqual(keys.get('POST', path, { ...intent, expected_revision: 5 }), key);
});

test('every static app control exists exactly once and both visual players keep sandbox isolation', async () => {
  const html = await readFile(new URL('../../video_workbench/web/index.html', import.meta.url), 'utf8');
  const app = await readFile(new URL('../../video_workbench/web/app.mjs', import.meta.url), 'utf8');
  const ids = [...html.matchAll(/\bid="([^"]+)"/g)].map(match => match[1]);
  assert.equal(new Set(ids).size, ids.length);
  const controls = [...app.matchAll(/\$\('([^']+)'\)/g)].map(match => match[1]);
  for (const id of controls) assert.ok(ids.includes(id), `missing control ${id}`);
  assert.match(html, /id="scene-preview"[^>]*sandbox=""/);
  assert.match(html, /id="project-player-frame"[^>]*sandbox=""/);
});
