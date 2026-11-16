import { test } from 'node:test';
import assert from 'node:assert/strict';
import { processContinuitySeekTimes } from '../../video_workbench/motion_renderer/observe.mjs';

test('process continuity seeks reverse through action centers and repeat both endpoints',()=>{
 const timeline={duration_seconds:8,actions:[{start:1,duration:1},{start:3,duration:2}]};
 assert.deepEqual(processContinuitySeekTimes(timeline),[8-1/24,0,4,1.5,.375,.6,8-1/24,0]);
});

test('the additional continuity evidence stays bounded for a maximal event timeline',()=>{
 const timeline={duration_seconds:60,actions:Array.from({length:256},(_,ordinal)=>({start:ordinal/5,duration:.1}))};
 const times=processContinuitySeekTimes(timeline);
 assert.equal(times.length,30);
 assert.equal(times[0],60-1/24);
 assert.equal(times[1],0);
 assert.equal(times.at(-1),0);
 assert.equal(times.at(-2),60-1/24);
 assert.ok(times.every(time=>time>=0&&time<60));
 assert.ok(times[2]>times[3]);
 assert.equal(times[2],timeline.actions[245].start+.05);
});

test('transition probes never exceed a short first segment',()=>{
 const timeline={duration_seconds:.25,actions:[{start:.01,duration:.1}]};
 const times=processContinuitySeekTimes(timeline);
 assert.ok(times.every(time=>time>=0&&time<=.25-1/24));
 assert.equal(times.at(-3),.25-1/24);
});
