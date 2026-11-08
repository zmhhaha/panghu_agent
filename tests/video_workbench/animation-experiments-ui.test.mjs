import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {experimentIdentity, experimentLocalUrl, experimentVersions, experimentPlayable, experimentInitialSelection,
  experimentComparison, experimentFrameTime, experimentIssuePayload,
  experimentSourceLabel, experimentStatusLabel, experimentTextDifference, experimentTechnicalLabel} from '../../video_workbench/animation_lab_web/experiment-model.mjs';

const root = new URL('https://video-service.panghuer.top/dev/animations/');
const left = {id: 'dev-v1', label: 'v1', source: 'agent', prompt_revision: 'v1',
  identity: 'frozen-v1',
  input_role: 'development', input_identity: 'goal-one', duration_seconds: 8, status: 'rendered',
  media_url: '/dev/animations/api/experiments/identity-tracking/media/dev-v1.mp4'};
const right = {...left, id: 'dev-v2', identity: 'frozen-v2', label: 'v2', prompt_revision: 'v2'};
const experiment = {id: 'identity-tracking', identity: 'frozen-definition', versions: [left, right]};

test('initial comparison selects actual playable works while preserving failed records for inspection', () => {
  const failed = {...left, id: 'original-failed', status: 'failed', media_url: null};
  assert.deepEqual(experimentInitialSelection([failed, left, right], root), ['dev-v1', 'dev-v2']);
  assert.deepEqual(experimentInitialSelection([failed, left], root), ['dev-v1']);
  assert.deepEqual(experimentInitialSelection([failed], root), ['original-failed']);
  assert.deepEqual(experimentInitialSelection([], root), []);
  assert.deepEqual(experimentInitialSelection(null, root), []);
  assert.deepEqual(experimentInitialSelection([failed, left, right], root, ['original-failed', 'dev-v1']), ['original-failed', 'dev-v1']);
  assert.deepEqual(experimentInitialSelection([failed, left, right], root, ['dev-v1', '']), ['dev-v1']);
  assert.deepEqual(experimentInitialSelection([failed, left], root, ['dev-v1', 'removed-version']), ['dev-v1']);
});

test('default comparison uses timestamps and a common recovery identity rather than directory order', () => {
  const make = (base, id, recovery, created_at) => ({...base, id,
    comparison_input_identity: 'same-controlled-input', provenance: {created_at, recovery: {identity: recovery}}});
  const oldV1 = make(left, 'z-old-v1', 'old-recovery', 10);
  const oldV2 = make(right, 'z-old-v2', 'old-recovery', 20);
  const newV1 = make(left, 'a-new-v1', 'new-recovery', 30);
  const newV2 = make(right, 'a-new-v2', 'new-recovery', 40);
  const versions = [newV2, oldV1, oldV2, newV1];
  assert.deepEqual(experimentInitialSelection(versions, root), ['a-new-v1', 'a-new-v2']);
  assert.deepEqual(versions.map(row => row.id), ['a-new-v2', 'z-old-v1', 'z-old-v2', 'a-new-v1']);
  // Neither another round nor another input may fill the missing half of a pair.
  assert.deepEqual(experimentInitialSelection([oldV1, newV2], root), ['a-new-v2']);
  assert.deepEqual(experimentInitialSelection([newV1, {...newV2, comparison_input_identity: 'different-input'}], root), ['a-new-v2']);
  assert.deepEqual(experimentInitialSelection([newV1, {...newV2, input_role: 'held_out'}], root), ['a-new-v2']);
  assert.deepEqual(experimentInitialSelection([newV1, {...newV2, media_url: 'https://other.example/video.mp4'}], root), ['a-new-v1']);
});

test('technical failures remain eligible for visual comparison instead of selecting only passed works', () => {
  assert.deepEqual(experimentInitialSelection([left, {...right, technical: {status: 'failed'}}], root), ['dev-v1', 'dev-v2']);
});

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

test('supplemental technical audit keeps the original failure and both reports visible without approving effects', () => {
  const source = readFileSync(new URL('../../video_workbench/animation_lab_web/experiments.mjs', import.meta.url), 'utf8');
  const functionSource = name => {
    const marker = `function ${name}(`;
    const start = source.indexOf(marker);
    const end = source.indexOf('\nfunction ', start + marker.length);
    assert.ok(start >= 0);
    return source.slice(start, end < 0 ? undefined : end);
  };
  class Element {
    constructor(tag) { this.tag = tag; this.children = []; this.value = ''; }
    set textContent(value) { this.value = String(value); this.children = []; }
    get textContent() { return this.value + this.children.map(child => child.textContent).join(''); }
    set innerHTML(_) { throw Error('Audit/source metadata must stay inert'); }
    append(...children) { this.children.push(...children); }
    setAttribute() {}
    addEventListener() {}
  }
  const original = {status: 'failed', issues: ['object_identity_not_visible']};
  const version = {...left, original_technical: original,
    technical: {status: 'passed', issues: [], aesthetic_approval: false},
    technical_reassessment: {verifier_revision: 'svg-label-geometry-v2', identity: 'audit-identity'},
    report_url: '/dev/animations/api/experiments/identity-tracking/runs/dev-v1/report.json',
    assessment_url: '/dev/animations/api/experiments/identity-tracking/runs/dev-v1/assessment.json',
    aesthetic_status: 'unreviewed'};
  assert.equal(experimentTechnicalLabel(version), '技术复核通过（原检查失败） · 效果待评价');
  const frozen = JSON.stringify(version);
  const context = vm.createContext({document: {createElement: tag => new Element(tag)}, version, root,
    experimentSourceLabel, experimentStatusLabel, experimentTechnicalLabel, experimentPlayable,
    experimentLocalUrl, fps: () => 24, media: [], clock: 0});
  const rendered = vm.runInContext(`${['node', 'jsonText', 'metadataDetails', 'renderVersion'].map(functionSource).join('\n')}\nrenderVersion(version, 0)`, context);
  const descendants = element => [element, ...element.children.flatMap(descendants)];
  const all = descendants(rendered);
  assert.ok(rendered.textContent.includes('技术复核通过（原检查失败） · 效果待评价'));
  const links = all.filter(element => element.tag === 'a');
  assert.equal(links.find(link => link.textContent.includes('查看原始检查报告')).href,
    experimentLocalUrl(version.report_url, root));
  assert.equal(links.find(link => link.textContent.includes('查看技术复核报告')).href,
    experimentLocalUrl(version.assessment_url, root));
  const facts = JSON.parse(all.find(element => element.tag === 'pre').textContent);
  assert.equal(facts.original_technical.status, 'failed');
  assert.equal(facts.current_technical.status, 'passed');
  assert.equal(facts.current_technical.aesthetic_approval, false);
  assert.equal(facts.technical_reassessment.verifier_revision, 'svg-label-geometry-v2');
  assert.equal(JSON.stringify(version), frozen, 'Displaying a correction cannot rewrite frozen version data');
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
    let finishOld, failOld, finishNew, oldPauses = 0, newPauses = 0, clears = 0;
    let notice = 'Current selection warning';
    const oldPending = new Promise((resolve, reject) => { finishOld = resolve; failOld = reject; });
    const newPending = new Promise(resolve => { finishNew = resolve; });
    const makeRow = (id, pending, pause) => ({version: {...left, id}, error: false,
      element: {readyState: 2, currentTime: 0, play: () => pending, pause}});
    const oldRows = [makeRow('old', oldPending, () => oldPauses++)];
    const newRows = [makeRow('new', newPending, () => newPauses++)];
    const element = {textContent: ''};
    const context = vm.createContext({oldRows, newRows, $: () => element, experimentFrameTime,
      cancelAnimationFrame() {}, requestAnimationFrame: () => 1, performance: {now: () => 100},
      message(id, text = '') {
        assert.equal(id, 'experiment-feedback-message');
        assert.equal(text, '', 'Stale player must not emit a new-selection error');
        notice = text; clears++;
      },
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
    assert.equal(clears, 0, `stale ${staleOutcome} must not clear the replacement warning`);
    assert.equal(notice, 'Current selection warning');
    assert.equal(vm.runInContext('playing', context), true);
    finishNew(); await newPlay;
    assert.equal(clears, 1);
    assert.equal(notice, '');
    assert.equal(vm.runInContext('playing', context), true);
    vm.runInContext('pause()', context);
    assert.equal(newPauses, 1);
    assert.ok(oldPauses >= 1);
  }
});

test('successful current playback retry clears an earlier loading or browser rejection warning', async () => {
  const source = readFileSync(new URL('../../video_workbench/animation_lab_web/experiments.mjs', import.meta.url), 'utf8');
  const getFunction = (name, isAsync = false) => {
    const marker = `${isAsync ? 'async ' : ''}function ${name}(`;
    const start = source.indexOf(marker);
    const end = source.indexOf('\nfunction ', start + marker.length);
    assert.ok(start >= 0);
    return source.slice(start, end < 0 ? undefined : end);
  };
  for (const initialFailure of ['not-ready', 'browser-rejected']) {
    let notice = '', errors = 0, clears = 0, starts = 0, finishRetry;
    const retry = new Promise(resolve => { finishRetry = resolve; });
    const row = {version: {...left}, error: false, element: {
      readyState: initialFailure === 'not-ready' ? 1 : 2, currentTime: 0, pause() {},
      play() { starts++; return Promise.reject(Error('Synthetic browser rejection')); }}};
    const button = {textContent: ''};
    const context = vm.createContext({rows: [row], $: () => button, experimentFrameTime,
      cancelAnimationFrame() {}, requestAnimationFrame: () => 1, performance: {now: () => 100},
      message(id, text = '', error = false) {
        assert.equal(id, 'experiment-feedback-message');
        notice = text;
        if (error) errors++; else if (!text) clears++;
      }, totalDuration: () => 8, fps: () => 24, seek() {}, updateTime() {}});
    vm.runInContext(`let media = rows, clock = 0, playing = false, playbackFrame = null, playbackEpoch = 0,
      activeView = 'experiments', startClock = 0, startWall = 0;\n${getFunction('pause')}\n${getFunction('play', true)}`, context);
    await vm.runInContext('play()', context);
    assert.equal(errors, 1);
    assert.equal(starts, initialFailure === 'not-ready' ? 0 : 1);
    assert.match(notice, initialFailure === 'not-ready' ? /尚未加载完成/ : /浏览器未能开始播放/);
    const warning = notice;
    row.element.readyState = 2;
    row.element.play = () => { starts++; return retry; };
    const pending = vm.runInContext('play()', context);
    assert.equal(notice, warning, 'The warning is cleared only after playback actually succeeds');
    finishRetry(); await pending;
    assert.equal(notice, '');
    assert.equal(clears, 1);
    assert.equal(errors, 1);
    assert.equal(vm.runInContext('playing', context), true);
    assert.equal(button.textContent, '暂停');
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
