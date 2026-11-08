import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {experimentIdentity, experimentLocalUrl, experimentVersions, experimentPlayable,
  experimentComparison, experimentFrameTime, experimentIssuePayload,
  experimentSourceLabel, experimentStatusLabel, experimentTextDifference, experimentTechnicalLabel} from '../../video_workbench/animation_lab_web/experiment-model.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const left = {id: 'dev-v1', label: 'v1', source: 'agent', prompt_revision: 'v1',
  identity: 'frozen-v1',
  input_role: 'development', input_identity: 'goal-one', duration_seconds: 8, status: 'rendered',
  media_url: '/dev/animations/api/experiments/identity-tracking/media/dev-v1.mp4'};
const right = {...left, id: 'dev-v2', identity: 'frozen-v2', label: 'v2', prompt_revision: 'v2'};
const experiment = {id: 'identity-tracking', identity: 'frozen-definition', versions: [left, right]};

test('unrun definitions do not manufacture playable variants or model provenance', () => {
  assert.deepEqual(experimentVersions({versions: []}), []);
  assert.deepEqual(experimentVersions(null), []);
  assert.equal(experimentPlayable(null, root), false);
  assert.equal(experimentPlayable({...left, status: 'running'}, root), false);
  assert.equal(experimentPlayable({...left, status: 'failed'}, root), false);
  assert.equal(experimentPlayable({...left, status: 'generated'}, root), false);
  assert.equal(experimentPlayable({...left, media_url: null}, root), false);
  assert.equal(experimentPlayable({...left, duration_seconds: 0}, root), false);
  assert.equal(experimentPlayable(left, root), true);
  assert.equal(experimentSourceLabel(undefined), '来源待核对');
  assert.equal(experimentSourceLabel('component'), '手写组件实验');
  assert.equal(experimentStatusLabel('definition'), '实验定义 · 未运行');
  assert.equal(experimentStatusLabel('__proto__'), '状态待核对');
});

test('playable media and reports stay on the authenticated lab prefix without code URLs', () => {
  assert.equal(experimentLocalUrl(left.media_url, root), new URL(left.media_url, root).href);
  assert.equal(experimentLocalUrl('api/experiments/example/media.mp4', root), `${root}api/experiments/example/media.mp4`);
  for (const url of ['https://evil.example/video.mp4', '/oauth2/callback', 'javascript:alert(1)',
    'data:text/html,<script>alert(1)</script>', 'file:///C:/scene.html', '//evil.example/x',
    'https://name:secret@video-service.panghuer.top/dev/animations/media.mp4',
    '/dev/animations/../api/private', '/dev/animations/\\evil', '/dev/animations/media.mp4\n']) {
    assert.equal(experimentLocalUrl(url, root), null, url);
  }
});

test('held-out inputs cannot be mixed into the development comparison', () => {
  const heldout = {...right, id: 'hold-v2', input_role: 'held_out', input_identity: 'goal-two'};
  assert.deepEqual(experimentVersions({...experiment, versions: [left, right, heldout]}).map(row => row.id), ['dev-v1', 'dev-v2']);
  assert.deepEqual(experimentVersions({...experiment, versions: [left, right, heldout]}, 'held_out').map(row => row.id), ['hold-v2']);
  assert.equal(experimentComparison(left, heldout).allowed, false);
  assert.equal(experimentComparison(left, {...right, input_identity: 'other-goal'}).allowed, false);
  assert.equal(experimentComparison(left, left).allowed, false);
  assert.equal(experimentComparison(left, null).allowed, false);
  assert.equal(experimentComparison(left, right).allowed, true);
});

test('hand-authored and model outputs may be compared without calling it prompt improvement', () => {
  const reference = {...left, id: 'handwritten', source: 'component', prompt_revision: null};
  const comparison = experimentComparison(reference, right);
  assert.equal(comparison.allowed, true);
  assert.match(comparison.reason, /不能当作提示词/);
  assert.match(experimentComparison(left, right).reason, /还需核对/);
});

test('shared media time clamps each side to a real last frame and supports reverse seeking', () => {
  assert.equal(experimentFrameTime(5.5, 8, 24), 5.5);
  assert.equal(experimentFrameTime(9, 8, 24), 8 - 1 / 24);
  assert.equal(experimentFrameTime(9, 6, 24), 6 - 1 / 24);
  assert.equal(experimentFrameTime(-1, 8, 24), 0);
  assert.equal(experimentFrameTime(0, 8, 24), 0);
  assert.equal(experimentFrameTime(NaN, 8, 24), 0);
  assert.equal(experimentFrameTime(4, 0, 24), 0);
});

test('issue payload binds one actual version and a valid time interval to the frozen experiment', () => {
  const payload = experimentIssuePayload(experiment, left, {start_seconds: '2.25', end_seconds: '3', comment: '  A在交叉处难以追踪  '});
  assert.deepEqual(payload, {experiment_identity: 'frozen-definition', kind: 'issue', version_ids: ['dev-v1'],
    version_identities: {'dev-v1': 'frozen-v1'},
    comment: 'A在交叉处难以追踪', start_seconds: 2.25, end_seconds: 3, preference: null});
  assert.equal(experimentIdentity({...experiment, experiment_identity: 'new-binding'}), 'new-binding');
  for (const values of [{start_seconds: -1, end_seconds: 2}, {start_seconds: 4, end_seconds: 3},
    {start_seconds: 0, end_seconds: 8.01}, {start_seconds: NaN, end_seconds: 2},
    {start_seconds: 0, end_seconds: 2, comment: ''}]) {
    assert.equal(experimentIssuePayload(experiment, left, {comment: '问题', ...values}), null);
  }
  assert.equal(experimentIssuePayload(experiment, {...left, id: 'retired-version'}, {start_seconds: 0, end_seconds: 2, comment: '问题'}), null);
  assert.equal(experimentIssuePayload(experiment, {...left, identity: ''}, {start_seconds: 0, end_seconds: 2, comment: '问题'}), null);
  assert.equal(experimentIssuePayload({...experiment, identity: ''}, left, {start_seconds: 0, end_seconds: 2, comment: '问题'}), null);
});

test('text comparison exposes actual changed guidance and code without parsing generated markup', () => {
  const difference = experimentTextDifference('fixed\nguidance=v1\nend', 'fixed\nguidance=v2\nend');
  assert.match(difference, /− guidance=v1/);
  assert.match(difference, /\+ guidance=v2/);
  assert.equal(experimentTextDifference('<script>unsafe</script>', '<script>unsafe</script>'), '文本相同。');
  assert.match(experimentTextDifference(null, 'code'), /未完整提供/);
  assert.match(experimentTextDifference('a\nb', 'a\nb\nc'), /\+ c/);
});

test('rendering and technical outcomes stay independent from effect approval', () => {
  const renderedFailure = {...left, technical: {status: 'failed', issues: ['identity_lost']}};
  assert.equal(experimentPlayable(renderedFailure, root), true, 'failed technical evidence must not hide the actual rendered video');
  assert.match(experimentTechnicalLabel(renderedFailure), /技术检查失败/);
  assert.match(experimentTechnicalLabel(renderedFailure), /效果待评价/);
  assert.equal(experimentTechnicalLabel({...left, technical: {status: 'passed'}}), '技术检查通过 · 效果待评价');
  assert.equal(experimentTechnicalLabel({...left, technical: {status: 'unknown'}}), '技术检查待核验 · 效果待评价');
  assert.equal(experimentTechnicalLabel({...left, status: 'failed', technical: null}), '尚未执行技术检查 · 效果待评价');
  assert.equal(experimentTechnicalLabel({...left, technical: null}), '技术检查待核验 · 效果待评价');
});

test('a late play completion or rejection from the previous media cannot pause the new selection', async () => {
  const source = readFileSync(new URL('../../video_workbench/animation_lab_web/experiments.mjs', import.meta.url), 'utf8');
  const getFunction = (name, isAsync = false) => {
    const marker = `${isAsync ? 'async ' : ''}function ${name}(`;
    const start = source.indexOf(marker);
    assert.ok(start >= 0);
    const end = source.indexOf('\nfunction ', start + marker.length);
    return source.slice(start, end < 0 ? undefined : end);
  };
  for (const staleOutcome of ['resolve', 'reject']) {
    let finishOld, failOld, finishNew, oldPauses = 0, newPauses = 0;
    const oldPending = new Promise((resolve, reject) => { finishOld = resolve; failOld = reject; });
    const newPending = new Promise(resolve => { finishNew = resolve; });
    const makeRow = (id, pending, pause) => ({version: {...left, id}, error: false,
      element: {readyState: 2, currentTime: 0, play: () => pending, pause}});
    const oldRows = [makeRow('old', oldPending, () => oldPauses++)];
    const newRows = [makeRow('new', newPending, () => newPauses++)];
    const element = {textContent: ''};
    const context = vm.createContext({oldRows, newRows, $: () => element, experimentFrameTime,
      cancelAnimationFrame() {}, requestAnimationFrame: () => 1, performance: {now: () => 100},
      message() { throw Error('Stale player must not emit a new-selection error'); },
      totalDuration: () => 8, fps: () => 24, seek() {}, updateTime() {}});
    vm.runInContext(`let media = oldRows, clock = 0, playing = false, playbackFrame = null, playbackEpoch = 0,
      activeView = 'experiments', startClock = 0, startWall = 0;\n${getFunction('pause')}\n${getFunction('play', true)}`, context);
    const oldPlay = vm.runInContext('play()', context);
    assert.equal(vm.runInContext('playing', context), true, 'pending startup must already respond to Pause');
    vm.runInContext('pause(); media = newRows', context);
    const newPlay = vm.runInContext('play()', context);
    if (staleOutcome === 'resolve') finishOld(); else failOld(Error('old media replaced'));
    await oldPlay;
    assert.equal(newPauses, 0, `stale ${staleOutcome} must not pause the replacement`);
    assert.equal(vm.runInContext('playing', context), true);
    finishNew(); await newPlay;
    assert.equal(vm.runInContext('playing', context), true);
    vm.runInContext('pause()', context);
    assert.equal(newPauses, 1);
    assert.ok(oldPauses >= 1);
  }
});

test('actual prompt and generated source are rendered only as text, never as a scene iframe', () => {
  const source = readFileSync(new URL('../../video_workbench/animation_lab_web/experiments.mjs', import.meta.url), 'utf8');
  const functionSource = name => {
    const marker = `function ${name}(`;
    const start = source.indexOf(marker);
    assert.ok(start >= 0);
    const end = source.indexOf('\nfunction ', start + marker.length);
    return source.slice(start, end < 0 ? undefined : end);
  };
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.value = ''; }
    set textContent(value) { this.value = String(value); this.children = []; }
    get textContent() { return this.value + this.children.map(child => child.textContent).join(''); }
    set innerHTML(_) { throw Error('Generated source must not be interpreted as HTML'); }
    append(...children) { this.children.push(...children); }
  }
  const malicious = '<script>fetch("/api/private")</script><iframe src="/oauth2/callback"></iframe>';
  const context = vm.createContext({document: {createElement: tag => new Element(tag)}, experimentSourceLabel,
    version: {...left, prompt_text: malicious, code_files: {'scene.html': malicious}, provenance: {model: '<img src=x onerror=alert(1)>'}}});
  const rendered = vm.runInContext(`${functionSource('node')}\n${functionSource('jsonText')}\n${functionSource('metadataDetails')}\nmetadataDetails(version)`, context);
  const descendants = element => [element, ...element.children.flatMap(descendants)];
  assert.ok(rendered.textContent.includes(malicious));
  assert.equal(descendants(rendered).filter(row => ['iframe', 'script', 'img'].includes(row.tag)).length, 0);
});
