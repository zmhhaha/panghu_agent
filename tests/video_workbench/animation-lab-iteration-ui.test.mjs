import test from 'node:test';
import assert from 'node:assert/strict';
import {iterationPullRequestUrl, iterationResourceUrl, isIterationOverview,
  iterationStateLabel, canReviewIteration, currentIterationAttemptEvents,
  iterationAttemptReceipt, canImportIterationReceipt, verifiedIterationCandidate, canAcceptIteration} from '../../video_workbench/animation_lab_web/model.mjs';

const origin = 'https://video-service.panghuer.top';
const base = '/dev/animations/';
const digest = 'a'.repeat(64);
const revision = 'b'.repeat(40);
const overview = () => ({module: 'canvas', current_cursor: digest, module_cursor: null,
  current_effective_feedback: [], preview_delta: [], tasks: []});
const candidate = () => {
  const proof = {task_identity: digest, module: 'canvas', source_revision: revision, commit: 'f'.repeat(40),
    run_slug: 'canvas-r1', report_identity: 'e'.repeat(64), run_identity: 'd'.repeat(64),
    artifact_sha256s: ['c'.repeat(64)], semantic_input_sha256: 'b'.repeat(64), publication_identity: '9'.repeat(64),
    case_id: 'canvas-queue', covered_feedback_keys: ['annotation:one'], uncovered_feedback_keys: [],
    verification_scope: 'frozen_report_and_developer_publication_binding',
    candidate_url: '/dev/animations/#evaluation-canvas-r1-' + 'd'.repeat(16)};
  return {task_id: digest, state: 'candidate_ready', current_feedback_matches: true,
    package: {identity: digest, source_revision: revision, module: 'canvas', feedback: [{key: 'annotation:one', case_id: 'canvas-queue'}]},
    acceptance_scope: 'developer_sample_only', production_adopted: false,
    events: [{state: 'candidate_ready', receipt: {commit: proof.commit, run_slug: proof.run_slug,
      report_identity: proof.report_identity, candidate_url: proof.candidate_url}, verified_candidate: proof}]};
};

test('PR action accepts only this repository and an exact positive pull request number', () => {
  const pr = 'https://github.com/zmhhaha/panghu_agent/pull/123';
  assert.equal(iterationPullRequestUrl(pr), pr);
  for (const value of [undefined, null, '', 'javascript:alert(1)',
    'http://github.com/zmhhaha/panghu_agent/pull/123',
    'https://github.com/other/panghu_agent/pull/123',
    'https://github.com/zmhhaha/other/pull/123',
    'https://github.com.evil.invalid/zmhhaha/panghu_agent/pull/123',
    'https://github.com@evil.invalid/zmhhaha/panghu_agent/pull/123',
    'https://user:token@github.com/zmhhaha/panghu_agent/pull/123',
    pr + '?token=secret', pr + '#comment', pr + '/', pr + '\n',
    'https://github.com/zmhhaha/panghu_agent/pull/0',
    'https://github.com/zmhhaha/panghu_agent/pull/0123',
    'https://github.com/zmhhaha/panghu_agent/issues/123']) {
    assert.equal(iterationPullRequestUrl(value), null, String(value));
  }
});

test('candidate and evidence links remain inside the authenticated developer path', () => {
  const path = base + 'evaluations/canvas-r1/report.html';
  assert.equal(iterationResourceUrl(path, origin, base), origin + path);
  assert.equal(iterationResourceUrl(base + '?candidate=abc', origin, base), origin + base + '?candidate=abc');
  const frozen = base + '#evaluation-canvas-r1-' + 'd'.repeat(16);
  assert.equal(iterationResourceUrl(frozen, origin, base), origin + frozen);
  assert.equal(iterationResourceUrl('/evaluations/report.html', origin, '/'), origin + '/evaluations/report.html');
  for (const path of ['https://evil.invalid/report', '//evil.invalid/report', '/oauth2/sign_out',
    '/dev/animations-evil/report', base + '../private', base + '%2e%2e/private',
    base + '%2e%2e%2fprivate', base + '%5cprivate', base + '%00private', base + '\\private']) {
    assert.equal(iterationResourceUrl(path, origin, base), null, path);
  }
  assert.equal(iterationResourceUrl(path, origin, '//dev/animations/'), null);
});

test('late summaries from a different module or malformed cursors cannot enable task creation', () => {
  assert.equal(isIterationOverview(overview(), 'canvas'), true);
  assert.equal(isIterationOverview({...overview(), module_cursor: 'b'.repeat(64)}, 'canvas'), true);
  assert.equal(isIterationOverview(overview(), 'three'), false);
  for (const replacement of [{module: ''}, {current_cursor: ''}, {current_cursor: null},
    {module_cursor: 'invalid'}, {module_cursor: undefined}, {tasks: {}},
    {preview_delta: undefined}, {current_effective_feedback: 'old feedback'}]) {
    assert.equal(isIterationOverview({...overview(), ...replacement}, 'canvas'), false);
  }
  assert.equal(isIterationOverview(null, 'canvas'), false);
});

test('download, synchronization, PR and developer acceptance have distinct status labels', () => {
  const states = ['prepared', 'synced', 'running', 'pr_open', 'candidate_ready', 'accepted', 'rejected', 'failed', 'canceled'];
  const labels = states.map(iterationStateLabel);
  assert.equal(new Set(labels).size, states.length);
  assert.match(iterationStateLabel('prepared'), /待同步/);
  assert.match(iterationStateLabel('synced'), /待执行/);
  assert.match(iterationStateLabel('accepted'), /开发样例/);
  assert.doesNotMatch(iterationStateLabel('accepted'), /生产|合入/);
  assert.match(iterationStateLabel('unexpected-success'), /未知/);
});

test('only a current candidate with an exact task and source binding can receive sample acceptance', () => {
  assert.equal(canReviewIteration(candidate()), true);
  for (const state of ['prepared', 'synced', 'running', 'pr_open', 'accepted', 'rejected', 'failed', 'canceled'])
    assert.equal(canReviewIteration({...candidate(), state}), false);
  for (const replacement of [{current_feedback_matches: false}, {current_feedback_matches: undefined},
    {task_id: 'invalid'}, {acceptance_scope: 'production'}, {production_adopted: true},
    {package: {...candidate().package, identity: 'c'.repeat(64)}},
    {package: {...candidate().package, source_revision: 'unknown'}},
    {package: {...candidate().package, module: 'all'}}, {package: undefined}]) {
    assert.equal(canReviewIteration({...candidate(), ...replacement}), false);
  }
  assert.equal(canReviewIteration(null), false);
});

test('a self-reported candidate URL cannot replace matching frozen-publication verification', () => {
  const original = candidate();
  assert.ok(verifiedIterationCandidate(original));
  const event = original.events[0];
  for (const change of [{task_identity: '0'.repeat(64)}, {module: 'three'}, {source_revision: '0'.repeat(40)},
    {commit: '0'.repeat(40)}, {report_identity: '0'.repeat(64)}, {run_slug: 'other-run'},
    {run_identity: '0'.repeat(64)}, {artifact_sha256s: []}, {artifact_sha256s: ['invalid']},
    {semantic_input_sha256: 'invalid'}, {publication_identity: null}, {verification_scope: 'self_reported'},
    {candidate_url: '/dev/animations/?candidate=unconsumed'}]) {
    const modified = {...original, events: [{...event, verified_candidate: {...event.verified_candidate, ...change}}]};
    assert.equal(canReviewIteration(modified), false, JSON.stringify(change));
  }
  assert.equal(canReviewIteration({...original, events: [{...event, verified_candidate: undefined}]}), false);
});

test('an explicit new attempt cannot inherit the previous PR, commit or candidate acceptance proof', () => {
  const original = candidate();
  original.events[0].receipt.pr_url = 'https://github.com/zmhhaha/panghu_agent/pull/12';
  const next = {state: 'running', new_attempt: true, receipt: {run_id: 'second-run', branch: 'codex/second'}};
  const retried = {...original, state: 'running', events: [...original.events,
    {state: 'rejected', receipt: {comment: '继续改进'}}, next]};
  assert.deepEqual(currentIterationAttemptEvents(retried), [next]);
  assert.deepEqual(iterationAttemptReceipt(retried), {run_id: 'second-run', branch: 'codex/second'});
  assert.equal(verifiedIterationCandidate(retried), null);
  assert.equal(canReviewIteration({...retried, state: 'candidate_ready'}), false);
  assert.equal(retried.events[0].receipt.pr_url, 'https://github.com/zmhhaha/panghu_agent/pull/12');
});

test('stopped tasks retain an explicit receipt import path while accepted samples remain final', () => {
  for (const state of ['prepared', 'synced', 'running', 'pr_open', 'candidate_ready', 'rejected', 'failed', 'canceled'])
    assert.equal(canImportIterationReceipt({state}), true);
  for (const state of ['accepted', 'unknown', null]) assert.equal(canImportIterationReceipt({state}), false);
});

test('a verified single-case candidate cannot approve feedback from other cases', () => {
  const full = candidate();
  assert.equal(canAcceptIteration(full), true);
  const other = {key: 'annotation:two', case_id: 'other-canvas'};
  const event = full.events[0];
  const partial = {...full, package: {...full.package, feedback: [...full.package.feedback, other]},
    events: [{...event, verified_candidate: {...event.verified_candidate, uncovered_feedback_keys: [other.key]}}]};
  assert.equal(canReviewIteration(partial), true);
  assert.equal(canAcceptIteration(partial), false);
  const falseCoverage = {...partial, events: [{...partial.events[0], verified_candidate:
    {...partial.events[0].verified_candidate, covered_feedback_keys: ['annotation:one', other.key], uncovered_feedback_keys: []}}]};
  assert.equal(verifiedIterationCandidate(falseCoverage), null);
});
