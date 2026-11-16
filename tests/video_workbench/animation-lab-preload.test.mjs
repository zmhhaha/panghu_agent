import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {warmPreviewAssets} from '../../video_workbench/animation_lab_web/model.mjs';

const origin = 'https://video-service.panghuer.top';
const raw = new TextEncoder().encode('synthetic browser transport bytes');
const sha256 = createHash('sha256').update(raw).digest('hex');
const asset = {name: 'NotoSansCJK-Regular.ttc', sha256, bytes: raw.byteLength,
  asset_url: `/dev/animations/assets/${sha256}/NotoSansCJK-Regular.ttc`};
const variant = (...assets) => ({available: true, preload_assets: assets});
const response = (bytes = raw) => ({ok: true, redirected: false,
  arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength)});

test('comparison sides share one authenticated fetch and wait for the full response body', async () => {
  let calls = 0, release, finished = false;
  const body = new Promise(resolve => { release = resolve; });
  const progress = [];
  const warming = warmPreviewAssets([variant(asset), variant(asset)], {origin,
    onProgress: value => progress.push(value),
    fetcher: async (url, options) => {
      calls++;
      assert.equal(url, origin + asset.asset_url);
      assert.equal(options.credentials, 'same-origin');
      assert.equal(options.cache, undefined); // Reuse the normal private browser cache.
      return {ok: true, redirected: false, arrayBuffer: () => body};
    }}).then(() => { finished = true; });
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(finished, false);
  release(response().arrayBuffer());
  await warming;
  assert.equal(finished, true);
  assert.deepEqual(progress, [{completed: 0, total: 1}, {completed: 1, total: 1}]);
});

test('invalid, external, or unavailable asset declarations cannot dispatch arbitrary requests', async () => {
  for (const change of [
    {name: 'animation-lab.db'}, {sha256: 'bad'}, {bytes: 0}, {bytes: 33 * 1024 * 1024},
    {asset_url: 'https://attacker.invalid/font'}, {asset_url: '//attacker.invalid/font'},
    {asset_url: asset.asset_url + '?token=secret'},
    {asset_url: asset.asset_url.replace(sha256, '0'.repeat(64))}
  ]) {
    await assert.rejects(warmPreviewAssets([variant({...asset, ...change})], {origin,
      fetcher: () => assert.fail('Invalid metadata must not dispatch.')}), /资源清单/);
  }
  await warmPreviewAssets([{available: false}], {origin,
    fetcher: () => assert.fail('Unavailable engine must not load resources.')});
});

test('HTTP, redirect, truncated body and hash failures stop without an automatic retry', async () => {
  for (const result of [
    {...response(), ok: false}, {...response(), redirected: true},
    response(raw.slice(1)), response(new Uint8Array(raw.byteLength))
  ]) {
    let calls = 0;
    await assert.rejects(warmPreviewAssets([variant(asset)], {origin,
      fetcher: async () => { calls++; return result; }}), /动画资源/);
    assert.equal(calls, 1);
  }
});

test('switching cases aborts a pending body and prevents a late success progress event', async () => {
  const controller = new AbortController();
  let release, requestSignal;
  const progress = [];
  const warming = warmPreviewAssets([variant(asset)], {origin, signal: controller.signal,
    onProgress: value => progress.push(value), fetcher: async (_, options) => {
      requestSignal = options.signal;
      return {ok: true, redirected: false, arrayBuffer: () => new Promise(resolve => { release = resolve; })};
    }});
  await Promise.resolve();
  controller.abort();
  await assert.rejects(warming, error => error.name === 'AbortError');
  assert.equal(requestSignal.aborted, true);
  release(await response().arrayBuffer());
  await Promise.resolve();
  assert.deepEqual(progress, [{completed: 0, total: 1}]);
});

test('cold resources have a separate bounded deadline and a fresh explicit attempt can succeed', async () => {
  let requestSignal;
  await assert.rejects(warmPreviewAssets([variant(asset)], {origin, timeoutMs: 10,
    fetcher: (_, options) => { requestSignal = options.signal; return new Promise(() => {}); }}), /资源加载超时/);
  assert.equal(requestSignal.aborted, true);
  await warmPreviewAssets([variant(asset)], {origin, fetcher: async () => response()});
  const controller = new AbortController(); controller.abort();
  await assert.rejects(warmPreviewAssets([variant(asset)], {origin, signal: controller.signal,
    fetcher: () => assert.fail('An already cancelled case must not dispatch.')}), error => error.name === 'AbortError');
});

test('the fixed-resource cold download deadline is ten minutes and always cleared', async () => {
  const originalSetTimeout = globalThis.setTimeout, originalClearTimeout = globalThis.clearTimeout;
  let timeout, cleared;
  globalThis.setTimeout = (callback, milliseconds) => {
    timeout = milliseconds;
    return originalSetTimeout(callback, milliseconds);
  };
  globalThis.clearTimeout = timer => { cleared = true; return originalClearTimeout(timer); };
  try {
    await warmPreviewAssets([variant(asset)], {origin, fetcher: async () => response()});
    assert.equal(timeout, 600000);
    assert.equal(cleared, true);
  } finally {
    globalThis.setTimeout = originalSetTimeout; globalThis.clearTimeout = originalClearTimeout;
  }
});
