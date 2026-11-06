import test from 'node:test';
import assert from 'node:assert/strict';
import { projectDurationSummary, durationSummaryLines, plannedSceneSeconds } from '../../video_workbench/web/duration.mjs';

function scene(id, estimate, actual, options = {}) {
  return { id, duration_seconds: estimate, production_mode: 'motion',
    motion: { mode: 'motion', head_version_id: `v-${id}` },
    head_audio_version_id: `a-${id}`, audio_versions: [{ id: `a-${id}`, duration_seconds: actual }], ...options };
}

function render(project, options = {}) {
  return { id: 'whole', status: 'completed', actual_duration_seconds: 161, content_state: 'current',
    created_at: 1, purpose: 'draft', scenes: project.scenes.map(s => ({ scene_id: s.id,
      visual_version_id: s.motion.head_version_id, audio_version_id: s.head_audio_version_id })), ...options };
}

test('five-shot feedback separates estimated 120, recorded 157 and measured 161 seconds', () => {
  const project = { scenes: [26, 29, 37, 35, 30].map((actual, i) => scene(`s${i}`, 24, actual)) };
  const result = projectDurationSummary(project, [render(project)], { endHoldSeconds: 4 });
  assert.equal(result.estimatedSeconds, 120);
  assert.equal(result.audioSeconds, 157);
  assert.equal(result.audioCount, 5);
  assert.equal(result.plannedSeconds, 161);
  assert.equal(result.rendered.actual_duration_seconds, 161);
  const lines = durationSummaryLines(result).join('\n');
  assert.match(lines, /目标时长尚未单独记录/);
  assert.match(lines, /方案预计 120 秒/);
  assert.match(lines, /配音 157 秒（5\/5 镜）/);
  assert.match(lines, /整片草稿：实际 161 秒/);
  assert.match(lines, /录音比方案预计长 37 秒/);
});

test('a superseded continuity draft is not the current project measured total', () => {
  const project = { scenes: [scene('s1', 10, 12)] };
  const frozen = render(project, { continuity_version_id: 'c-old', continuity_current: false });
  assert.equal(projectDurationSummary(project, [frozen]).rendered, null);
  assert.equal(projectDurationSummary(project, [{ ...frozen, continuity_current: true }]).rendered.actual_duration_seconds, 161);
});

test('pending, unadopted and stale audio do not become an apparent project total', () => {
  const project = { scenes: [scene('current', 10, 12), scene('stale', 10, 15, { audio_stale: true }),
    scene('candidate', 10, 50, { head_audio_version_id: null }),
    scene('stale-version', 10, 20, { audio_versions: [{ id: 'a-stale-version', duration_seconds: 20, stale: true }] })] };
  const result = projectDurationSummary(project);
  assert.equal(result.audioCount, 1);
  assert.equal(result.audioSeconds, 12);
  assert.equal(result.plannedSeconds, null);
  assert.match(durationSummaryLines(result).join('\n'), /1\/4 镜，尚非全片总时长/);
  assert.equal(result.overEstimateSeconds, 0);
});

test('dynamic duration follows recording, static duration retains configured minimum, both align to frames', () => {
  assert.equal(plannedSceneSeconds(scene('d', 30, 10)), 10);
  assert.equal(plannedSceneSeconds(scene('s', 30, 10, { motion: { mode: 'static' } })), 30);
  assert.equal(plannedSceneSeconds(scene('padding', 30, 10.01, { leading_seconds: .2, trailing_seconds: 5 }),
    { endHoldSeconds: 4 }), Math.ceil(15.21 * 24) / 24);
});

test('only a completed measured whole render matching the current adopted media is shown', () => {
  const project = { scenes: [scene('one', 5, 6), scene('two', 5, 7)] };
  const good = render(project);
  const bad = [render(project, { status: 'queued' }), render(project, { content_state: 'changed' }),
    render(project, { silent: true }), render(project, { actual_duration_seconds: null, duration_seconds: 161 }),
    render(project, { actual_duration_seconds: Infinity }), render(project, { actual_duration_seconds: '161' }),
    render(project, { scenes: good.scenes.slice(0, 1) }), render(project, { scenes: good.scenes.toReversed() }),
    render(project, { scenes: good.scenes.map(s => ({ ...s, audio_version_id: 'older' })) }),
    render(project, { scenes: good.scenes.map(s => ({ ...s, visual_version_id: 'other' })) })];
  assert.equal(projectDurationSummary(project, bad).rendered, null);
  assert.equal(projectDurationSummary(project, [...bad, good]).rendered.id, good.id);
  assert.equal(projectDurationSummary(project, [good, render(project, { id: 'newer', created_at: 2 })]).rendered.id, 'newer');
});

test('an empty project has no complete audio or final duration', () => {
  const result = projectDurationSummary({ scenes: [] });
  assert.equal(result.audioComplete, false);
  assert.equal(result.plannedSeconds, null);
  assert.equal(result.rendered, null);
});
