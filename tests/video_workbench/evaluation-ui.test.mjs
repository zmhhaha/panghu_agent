import test from 'node:test';
import assert from 'node:assert/strict';
import {isCurrentEvaluation, evaluationFindingLocation, currentEvaluationDecisions,
  localResourceUrl} from '../../video_workbench/animation_lab_web/model.mjs';

function fixture() {
  const evaluation = {id: 'diagram-run', run_identity: 'frozen-run', report_identity: 'frozen-report',
    technical: {status: 'passed'}, model: {status: 'reviewed', preference: 'B'},
    findings: [{id: 'layout-1', source: 'model', variant_id: 'layout-focus', category: 'layout',
      severity: 'major', region: {x: .2, y: .1, width: .4, height: .3},
      frames: [{id: 'frame_B_004', variant_id: 'layout-focus', clip_id: 'scene-2',
        time_seconds: 12.5, sha256: 'a'.repeat(64), image_url: '/evaluations/diagram-run/frame.jpg'}]}], decisions: []};
  const currentCase = {id: 'evaluation-diagram-run', evaluation: {...evaluation},
    variants: [{id: 'candidate', duration_seconds: 12}, {id: 'layout-focus', duration_seconds: 18}]};
  return {evaluation, currentCase};
}

test('machine finding requires the exact evaluated run and report, not matching fixture names', () => {
  const {evaluation, currentCase} = fixture();
  assert.equal(isCurrentEvaluation(evaluation, currentCase), true);
  assert.equal(isCurrentEvaluation(evaluation, {...currentCase, evaluation: undefined}), false);
  for (const key of ['id', 'run_identity', 'report_identity']) {
    for (const replacement of [undefined, '', 'older-version', 5]) {
      const changed = {...evaluation, [key]: replacement};
      assert.equal(isCurrentEvaluation(changed, currentCase), false);
      assert.equal(evaluationFindingLocation(changed, currentCase, 'layout-1', 'frame_B_004'), null);
    }
  }
});

test('a machine frame locates its actual variant on the global cross-shot clock without mutating region', () => {
  const {evaluation, currentCase} = fixture();
  const location = evaluationFindingLocation(evaluation, currentCase, 'layout-1', 'frame_B_004');
  assert.deepEqual(location, {variant_id: 'layout-focus', time: 12.5, region: {x: .2, y: .1, width: .4, height: .3}});
  location.region.x = 0;
  assert.equal(evaluation.findings[0].region.x, .2);
  assert.equal(evaluationFindingLocation(evaluation, currentCase, 'unseen-issue', 'frame_B_004'), null);
  assert.equal(evaluationFindingLocation(evaluation, currentCase, 'layout-1', 'invented-frame'), null);
});

test('wrong-side and missing frame evidence cannot locate a current preview', () => {
  for (const patch of [
    {variant_id: 'candidate'}, {variant_id: 'missing'}, {time_seconds: -1},
    {time_seconds: NaN}, {time_seconds: 18.1}, {clip_id: ''}, {sha256: 'not-a-digest'},
  ]) {
    const {evaluation, currentCase} = fixture();
    Object.assign(evaluation.findings[0].frames[0], patch);
    assert.equal(evaluationFindingLocation(evaluation, currentCase, 'layout-1', 'frame_B_004'), null);
  }
  const {evaluation, currentCase} = fixture();
  evaluation.findings[0].variant_id = 'candidate';
  evaluation.findings[0].frames[0].variant_id = 'candidate';
  assert.equal(evaluationFindingLocation(evaluation, currentCase, 'layout-1', 'frame_B_004'), null,
    'a frame within the longer side cannot borrow its duration for the shorter side');
});

test('invalid or unavailable model reviews do not become locatable machine findings', () => {
  for (const status of ['not_run', 'unavailable', 'failed', 'invalid', undefined]) {
    const {evaluation, currentCase} = fixture();
    evaluation.model.status = status;
    assert.equal(evaluationFindingLocation(evaluation, currentCase, 'layout-1', 'frame_B_004'), null);
    evaluation.findings[0].source = 'technical';
    assert.equal(evaluationFindingLocation(evaluation, currentCase, 'layout-1', 'frame_B_004').time, 12.5,
      'technical evidence remains independent of model availability');
  }
});

test('machine issue rectangles must stay normalized to the actual picture', () => {
  for (const region of [
    {x: .9, y: 0, width: .2, height: .1}, {x: 0, y: .9, width: .1, height: .2},
    {x: 0, y: 0, width: 0, height: .1}, {x: 0, y: 0, width: .1, height: Infinity},
    {x: 0, y: 0, width: '0.1', height: .1},
  ]) {
    const {evaluation, currentCase} = fixture();
    evaluation.findings[0].region = region;
    assert.equal(evaluationFindingLocation(evaluation, currentCase, 'layout-1', 'frame_B_004'), null);
  }
  const {evaluation, currentCase} = fixture();
  evaluation.findings[0].region = null;
  assert.deepEqual(evaluationFindingLocation(evaluation, currentCase, 'layout-1', 'frame_B_004'),
    {variant_id: 'layout-focus', time: 12.5, region: null});
});

test('developer decisions use only current known findings and preserve latest explicit choice', () => {
  const {evaluation, currentCase} = fixture();
  evaluation.decisions = [
    {id: 'one', finding_id: 'layout-1', decision: 'needs_fix'},
    {id: 'two', finding_id: 'layout-1', decision: 'defer', report_identity: 'frozen-report'},
    {id: 'stale', finding_id: 'layout-1', decision: 'needs_fix', report_identity: 'old-report'},
    {id: 'unknown', finding_id: 'unseen-finding', decision: 'needs_fix'},
    {id: 'invalid', finding_id: 'layout-1', decision: 'adopt'},
  ];
  assert.deepEqual(currentEvaluationDecisions(evaluation, currentCase), [evaluation.decisions[1]]);
  assert.deepEqual(currentEvaluationDecisions({...evaluation, report_identity: 'old-report'}, currentCase), []);
});

test('evidence and task links stay on this developer server', () => {
  const origin = 'http://127.0.0.1:8766';
  assert.equal(localResourceUrl('/api/evaluations/diagram-run/revision-task', origin),
    origin + '/api/evaluations/diagram-run/revision-task');
  for (const value of ['https://third-party.example/image', '//third-party.example/image',
    '/\\third-party.example/image', 'javascript:alert(1)', undefined, 123]) {
    assert.equal(localResourceUrl(value, origin), null);
  }
});
