import test from 'node:test';
import assert from 'node:assert/strict';
import {isCurrentAnnotation, issueLocation} from '../../video_workbench/animation_lab_web/model.mjs';

const identity = 'frozen-case-a';
const variants = [{id: 'baseline', duration_seconds: 10}, {id: 'candidate', duration_seconds: 10.6}];
const issue = () => ({case_identity: identity, variant_id: 'candidate', kind: 'issue',
  start_seconds: 4, end_seconds: 4.5, region: {x: .2, y: .1, width: .3, height: .4}});

test('review and preference membership requires an exact nonempty frozen identity', () => {
  for (const kind of ['issue', 'review', 'preference']) {
    assert.equal(isCurrentAnnotation({...issue(), kind}, identity), true);
    for (const bad of [undefined, null, '', 'previous-case', 123]) {
      assert.equal(isCurrentAnnotation({...issue(), kind, case_identity: bad}, identity), false);
    }
  }
  assert.equal(isCurrentAnnotation({case_identity: ''}, ''), false);
  assert.equal(isCurrentAnnotation(null, identity), false);
});

test('old or missing identities cannot locate current frames even when variant IDs match', () => {
  for (const case_identity of ['old-frozen-case', undefined, null, '']) {
    assert.equal(issueLocation({...issue(), case_identity}, identity, variants), null);
  }
  assert.equal(issueLocation({...issue(), variant_id: 'retired-variant'}, identity, variants), null);
  assert.equal(issueLocation({...issue(), kind: 'preference'}, identity, variants), null);
});

test('a matching issue locates its exact side and copies a normalized region without changing the record', () => {
  const original = issue();
  const location = issueLocation(original, identity, variants);
  assert.deepEqual(location, {variant_id: 'candidate', time: 4, region: original.region});
  location.region.x = 0;
  assert.equal(original.region.x, .2);
  assert.deepEqual(issueLocation({...original, region: null, start_seconds: 10.5, end_seconds: 10.6}, identity, variants),
    {variant_id: 'candidate', time: 10.5, region: null});
});

test('a matching identity does not permit timing from another side or an invalid region', () => {
  for (const replacement of [
    {start_seconds: -1}, {start_seconds: NaN}, {end_seconds: 3},
    {variant_id: 'baseline', start_seconds: 10.5, end_seconds: 10.6},
    {region: {x: .9, y: 0, width: .2, height: .2}},
    {region: {x: 0, y: 0, width: 0, height: .2}},
    {region: {x: 0, y: Infinity, width: .1, height: .2}},
  ]) assert.equal(issueLocation({...issue(), ...replacement}, identity, variants), null);
});
