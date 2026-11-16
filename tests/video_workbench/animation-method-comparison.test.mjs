import test from 'node:test';
import assert from 'node:assert/strict';
import {createMethodComparison} from '../../video_workbench/animation_lab_web/method-comparison.mjs';

class Element {
  constructor(tag) {
    this.tagName = tag.toUpperCase(); this.children = []; this.dataset = {}; this.attributes = new Map();
    this.listeners = new Map(); this.className = ''; this.textContent = ''; this.disabled = false;
    this.value = ''; this.classList = {add: name => { this.className += ' ' + name; }};
    if (tag === 'video') {
      this.duration = NaN; this.readyState = 0; this.currentTime = 0; this.muted = false;
      this.playbackRate = 1; this.paused = true; this.pauseCalls = 0; this.loadCalls = 0;
      this.play = () => { this.paused = false; return this.playResult ?? Promise.resolve(); };
      this.pause = () => { this.paused = true; this.pauseCalls++; };
      this.load = () => { this.loadCalls++; };
    }
  }
  append(...items) { this.children.push(...items); }
  replaceChildren(...items) { this.children = items; }
  setAttribute(name, value) { this.attributes.set(name, value); }
  removeAttribute(name) { this.attributes.delete(name); if (name === 'src') this.src = ''; }
  addEventListener(type, handler) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(handler);
  }
  removeEventListener(type, handler) { this.listeners.get(type)?.delete(handler); }
  emit(type) { if (!this.disabled) for (const fn of [...this.listeners.get(type) ?? []]) fn({target: this}); }
}
const all = element => [element, ...element.children.flatMap(all)];
const version = (id, changes = {}) => ({id, title: '版本 ' + id, media_url: '/media/' + id, ...changes});
const deferred = () => { let resolve, reject; const promise = new Promise((a, b) => { resolve = a; reject = b; }); return {promise, resolve, reject}; };
const flush = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };
function harness(options = {}) {
  const frames = new Map(), selections = [], times = [], safeCalls = []; let sequence = 0, wall = 0;
  const root = new URL('https://video-service.example/dev/animations/');
  const player = createMethodComparison({createElement: tag => new Element(tag)}, {
    candidates: [version('a'), version('b'), version('c')], baselineId: 'a', bestId: 'b', root,
    now: () => wall,
    scheduleFrame: fn => { const id = ++sequence; frames.set(id, fn); return id; },
    cancelFrame: id => frames.delete(id),
    assetUrl: (value, suppliedRoot) => {
      safeCalls.push([value, suppliedRoot]);
      if (typeof value !== 'string' || !/^\/(media|source)\/[a-z]+$/.test(value)) return null;
      return new URL(value, suppliedRoot).href;
    },
    onSelectionChange: snapshot => selections.push(snapshot),
    onTimeChange: (time, snapshot) => times.push([time, snapshot]), ...options,
  });
  const control = role => all(player.element).find(row => row.dataset.role === 'comparison-' + role);
  return {player, frames, selections, times, safeCalls, root, control,
    videos: () => all(player.element).filter(row => row.tagName === 'VIDEO'),
    links: () => all(player.element).filter(row => row.tagName === 'A'),
    change(role, value) { const row = control(role); row.value = String(value); row.emit('change'); },
    click(role) { control(role).emit('click'); },
    ready(durations = [2, 6], state = 2) {
      this.videos().forEach((row, index) => { row.duration = durations[index]; row.readyState = state; row.emit('loadedmetadata'); });
    },
    seek(time) { control('seek').value = String(time); control('seek').emit('input'); },
    advance(milliseconds) {
      wall += milliseconds; const callbacks = [...frames.values()]; frames.clear(); callbacks.forEach(fn => fn(wall));
    },
  };
}

test('placeholder versions retain selectors but cannot play, seek or download unsafe media', () => {
  const h = harness({candidates: [version('a', {media_url: 'javascript:alert(1)', source_url: 'https://evil.example/source'}),
    version('b', {media_url: null}), version('b')], selection: {left: 'a', right: 'b'}});
  assert.equal(h.control('left').disabled, false); assert.equal(h.control('right').children.length, 3);
  for (const role of ['play', 'reset', 'previous', 'next', 'seek', 'speed']) assert.equal(h.control(role).disabled, true, role);
  assert.equal(h.videos().length, 0); assert.equal(h.links().length, 0);
  assert.equal(h.player.getTime('a'), null); assert.equal(h.player.seek('a', 1), false);
  assert.ok(h.safeCalls.every(([, suppliedRoot]) => suppliedRoot === h.root));
  assert.match(h.control('status').textContent, /尚无实际/); h.player.destroy();
});

test('the selected versions, single view and current-best/baseline labels are explicit', () => {
  const h = harness();
  assert.equal(h.player.getSelection().left, 'a'); assert.equal(h.player.getSelection().right, 'b');
  assert.match(h.control('left').children.find(row => row.value === 'a').textContent, /优化基线/);
  assert.match(h.control('right').children.find(row => row.value === 'b').textContent, /当前最佳/);
  h.change('right', 'c'); assert.equal(h.player.getSelection().right, 'c');
  h.change('left', 'c'); assert.equal(h.player.getSelection().right, ''); assert.equal(h.videos().length, 1);
  h.change('right', 'a'); assert.equal(h.videos().length, 2);
  h.change('right', ''); assert.equal(h.videos().length, 1); h.player.destroy();
});

test('safe video and source downloads retain sanitizer boundaries', () => {
  const h = harness({candidates: [version('a', {source_url: '/source/a'}), version('b')]});
  assert.equal(h.links().length, 3);
  assert.ok(h.links().every(row => row.href.startsWith('https://video-service.example/')));
  assert.ok(h.safeCalls.some(([value]) => value === '/source/a'));
  assert.ok(h.links().find(row => row.textContent === '下载源码').download); h.player.destroy();
  const failed = harness({assetUrl: () => { throw new Error('invalid'); }});
  assert.equal(failed.videos().length, 0); assert.equal(failed.links().length, 0); failed.player.destroy();
});

test('actual duration and readiness are required even when candidates declare a duration', () => {
  const h = harness({candidates: [version('a', {duration_seconds: 20}), version('b', {duration_seconds: 20})]});
  assert.equal(h.control('play').disabled, true); assert.equal(h.player.getTime('a'), null);
  h.ready([2, 6], 1); assert.equal(h.control('play').disabled, true); assert.equal(h.control('seek').disabled, false);
  for (const row of h.videos()) { row.readyState = 2; row.emit('loadeddata'); }
  assert.equal(h.control('play').disabled, false); h.player.destroy();
});

test('repeated canplay and metadata notifications do not seek media already at the requested time', () => {
  const h = harness({selection: {left: 'a', right: 'b', time: 1.25}}); h.ready([2, 6]);
  let writes = 0;
  for (const row of h.videos()) {
    let current = row.currentTime;
    Object.defineProperty(row, 'currentTime', {get: () => current, set: value => { current = value; writes++; }});
  }
  for (let pass = 0; pass < 5; pass++) for (const row of h.videos()) {
    row.emit('canplay'); row.emit('loadeddata'); row.emit('durationchange'); row.emit('loadedmetadata');
  }
  assert.equal(writes, 0); assert.equal(h.control('play').disabled, false);
  assert.equal(h.player.getSelection().time, 1.25); h.player.destroy();
});

test('canplay during playback never rewinds video to the last animation-clock sample', async () => {
  const h = harness(); h.ready([2, 6]); h.click('play'); await flush();
  const video = h.videos()[1]; video.currentTime = 0.1; video.emit('canplay'); video.emit('loadeddata');
  assert.equal(video.currentTime, 0.1); assert.equal(h.player.getSelection().playing, true);
  const short = h.videos()[0]; h.advance(2500); let writes = 0, current = short.currentTime;
  Object.defineProperty(short, 'currentTime', {get: () => current, set: value => { current = value; writes++; }});
  h.advance(20); h.advance(20); h.advance(20);
  assert.equal(writes, 0, 'holding the same ending must not trigger another seek each frame'); h.player.destroy();
});

test('unequal versions share original seconds and hold the shorter ending', () => {
  const h = harness(); h.ready([2, 6]); h.seek(5);
  assert.equal(h.player.getSelection().time, 5); assert.equal(h.player.getTime('b'), 5);
  assert.ok(Math.abs(h.player.getTime('a') - (2 - 1 / 24)) < 1e-9);
  assert.equal(h.videos()[0].currentTime, h.player.getTime('a'));
  assert.match(h.control('time').textContent, /5\.000 \/ 6\.000/);
  assert.ok(h.times.some(([time, snapshot]) => time === 5 && snapshot.time === 5)); h.player.destroy();
});

test('seeking pauses playback and frame stepping uses 24 fps with bounded reset', async () => {
  const h = harness(); h.ready(); h.click('play'); await flush(); assert.equal(h.player.getSelection().playing, true);
  h.seek(1); assert.equal(h.player.getSelection().playing, false); assert.equal(h.frames.size, 0);
  h.click('next'); assert.ok(Math.abs(h.player.getSelection().time - 25 / 24) < 1e-9);
  h.click('previous'); assert.equal(h.player.getSelection().time, 1);
  h.click('reset'); h.click('previous'); assert.equal(h.player.getSelection().time, 0); h.player.destroy();
});

test('speed choices drive the shared clock and stop at the longer ending', async () => {
  const h = harness(); h.ready([2, 6]); h.change('speed', 2); h.click('play'); await flush();
  assert.equal(h.frames.size, 1); assert.ok(h.videos().every(row => row.playbackRate === 2));
  h.advance(1500); assert.equal(h.player.getSelection().time, 3);
  assert.equal(h.videos()[0].paused, true); assert.ok(Math.abs(h.videos()[0].currentTime - (2 - 1 / 24)) < 1e-9);
  assert.equal(h.videos()[1].currentTime, 3);
  h.change('speed', 0.5); h.advance(2000); assert.equal(h.player.getSelection().time, 4);
  h.advance(4000); assert.equal(h.player.getSelection().time, 6);
  assert.equal(h.player.getSelection().playing, false); assert.equal(h.frames.size, 0); h.player.destroy();
});

test('a rejected play pauses both versions and does not leave an animation loop', async () => {
  const h = harness(); h.ready(); h.videos()[1].playResult = Promise.reject(new Error('blocked'));
  h.click('play'); await flush(); assert.equal(h.player.getSelection().playing, false);
  assert.ok(h.videos().every(row => row.paused)); assert.equal(h.frames.size, 0);
  assert.match(h.control('status').textContent, /未能开始播放/); h.player.destroy();
});

test('buffering never disables the pause action and prevents restarting until data returns', async () => {
  const h = harness(); h.ready(); h.click('play'); await flush();
  h.videos()[1].readyState = 1; h.videos()[1].emit('durationchange');
  assert.equal(h.control('play').disabled, false); assert.equal(h.control('play').textContent, '暂停');
  h.click('play'); assert.equal(h.player.getSelection().playing, false);
  assert.ok(h.videos().every(row => row.paused)); assert.equal(h.frames.size, 0);
  assert.equal(h.control('play').disabled, true);
  h.videos()[1].readyState = 2; h.videos()[1].emit('canplay');
  assert.equal(h.control('play').disabled, false); h.player.destroy();
});

test('a pending play request remains cancellable when media starts buffering', async () => {
  const h = harness(); h.ready(); const pending = deferred(); h.videos()[1].playResult = pending.promise;
  h.click('play'); h.videos()[1].readyState = 1; h.videos()[1].emit('durationchange');
  assert.equal(h.control('play').disabled, false); assert.equal(h.control('play').textContent, '取消开始');
  h.click('play'); assert.equal(h.player.getSelection().playing, false);
  pending.resolve(); await flush();
  assert.ok(h.videos().every(row => row.paused)); assert.equal(h.frames.size, 0); h.player.destroy();
});

test('audio comes from only the selected version and single view cannot keep an absent audio side', () => {
  const h = harness(); h.ready(); assert.deepEqual(h.videos().map(row => row.muted), [false, true]);
  h.change('audio', 'right'); assert.deepEqual(h.videos().map(row => row.muted), [true, false]);
  h.change('audio', 'none'); assert.ok(h.videos().every(row => row.muted));
  h.change('audio', 'right'); h.change('right', ''); assert.equal(h.player.getSelection().audio, 'left');
  assert.equal(h.videos()[0].muted, false); h.player.destroy();
});

test('historical feedback seeking reveals an unseen version and waits for real metadata', () => {
  const h = harness(); h.ready();
  assert.equal(h.player.seek('c', 3.25), true); assert.equal(h.control('left').value, 'c');
  assert.equal(h.player.getTime('c'), null); assert.equal(h.control('play').disabled, true);
  h.ready([4, 6]); assert.equal(h.player.getTime('c'), 3.25); assert.equal(h.player.getTime('b'), 3.25);
  assert.equal(h.player.seek('missing', 1), false); assert.equal(h.player.seek('c', NaN), false);
  assert.equal(h.player.seek('c', 999), true); assert.ok(Math.abs(h.player.getTime('c') - (4 - 1 / 24)) < 1e-9);
  h.player.destroy();
});

test('selection, clock, speed, audio and active playback restore after remount', async () => {
  const h = harness(); h.ready(); h.seek(1.25); h.change('speed', 1.5); h.change('audio', 'right');
  h.click('play'); await flush(); h.advance(100); const saved = h.player.getSelection();
  const beforeDestroy = h.selections.length; h.player.destroy(); assert.equal(h.selections.length, beforeDestroy);
  const restored = harness({selection: saved}); assert.equal(restored.control('play').disabled, true);
  restored.ready(); await flush();
  assert.deepEqual(restored.player.getSelection(), saved);
  assert.equal(restored.videos()[1].muted, false); assert.equal(restored.videos()[0].playbackRate, 1.5);
  assert.equal(restored.frames.size, 1); restored.player.destroy();
});

test('destruction releases frames, source handles and every listener without modifying restoration callbacks', async () => {
  const h = harness(); h.ready(); h.click('play'); await flush();
  const elements = all(h.player.element), videos = h.videos(), callbacks = h.selections.length;
  h.player.destroy(); assert.equal(h.frames.size, 0); assert.equal(h.selections.length, callbacks);
  assert.ok(videos.every(row => row.paused && row.src === '' && row.loadCalls === 1));
  assert.ok(elements.every(row => [...row.listeners.values()].every(set => set.size === 0)));
  assert.equal(h.player.seek('a', 1), false); h.player.destroy();
});

test('late play promises from an older request cannot interrupt a newer playback', async () => {
  const h = harness(); h.ready(); const old = deferred(); h.videos()[0].playResult = old.promise;
  h.click('play'); h.click('play'); h.videos()[0].playResult = Promise.resolve(); h.click('play'); await flush();
  assert.equal(h.player.getSelection().playing, true); old.reject(new Error('obsolete')); await flush();
  assert.equal(h.player.getSelection().playing, true); assert.ok(h.videos().every(row => !row.paused));
  assert.equal(h.frames.size, 1); h.player.destroy();
});

test('a media load error stops playback without generating or retrying any media', async () => {
  const h = harness(); h.ready(); h.click('play'); await flush(); h.videos()[0].emit('error');
  assert.equal(h.player.getSelection().playing, false); assert.equal(h.control('play').disabled, true);
  assert.equal(h.frames.size, 0); assert.match(h.control('status').textContent, /没有自动重新生成/); h.player.destroy();
});
