/** Opt-in synthetic demo only. This module is not imported by the production app. */
import { useMemo, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { ApiError } from '../api';
import { AcceptancePanel } from './AcceptancePanel';
import type { AcceptanceClient, AcceptanceCommand, AcceptanceSnapshot, PowerPointReview, VisualReview } from './contracts';
import { syntheticSnapshot } from './fixtures';

function memoryClient(stale: boolean): AcceptanceClient {
  const snapshots = new Map<string, AcceptanceSnapshot>();
  function get(version: string) {
    if (!snapshots.has(version)) {
      const snapshot = syntheticSnapshot(version);
      if (stale) { snapshot.can_review = false; snapshot.acceptance_status = 'stale'; snapshot.pending_reasons.push('source_stale'); }
      snapshots.set(version, snapshot);
    }
    return snapshots.get(version)!;
  }
  return {
    load: async (_project, version) => structuredClone(get(version)),
    save: async (_project, version, command) => {
      const snapshot = get(version);
      if (command.expected_revision !== snapshot.revision) throw new ApiError('REVISION_CONFLICT', 409);
      if (!snapshot.can_review && command.kind !== 'revoke') throw new ApiError('SOURCE_STALE', 409);
      const { kind, expected_revision: _revision, binding: _binding, ...payload } = command;
      snapshot.revision += 1;
      snapshot.records.push({ id: `demo-record-${snapshot.revision}`, revision: snapshot.revision, kind, created_at: new Date().toISOString(), reviewer: { issuer: 'synthetic-demo', sub: '演示检查人（内存记录）' }, binding: structuredClone(snapshot.binding), payload, revoked: false, stale: false });
      if (kind === 'revoke') { const target = snapshot.records.find(record => record.id === (command as Extract<AcceptanceCommand, { kind: 'revoke' }>).record_id); if (target) target.revoked = true; }
      const latestVisual = [...snapshot.records].reverse().find(record => record.kind === 'visual_review');
      const latestPowerPoint = [...snapshot.records].reverse().find(record => record.kind === 'powerpoint_review');
      const verdicts = latestVisual && !latestVisual.revoked ? (latestVisual.payload as VisualReview).pages.map(page => page.verdict) : [];
      const checks = latestPowerPoint && !latestPowerPoint.revoked ? Object.values((latestPowerPoint.payload as PowerPointReview).checks) : [];
      snapshot.visual_status = verdicts.includes('needs_changes') ? 'needs_changes' : verdicts.length && verdicts.every(verdict => verdict === 'pass') ? 'passed' : 'pending';
      snapshot.powerpoint_status = checks.includes('fail') ? 'needs_changes' : checks.length && checks.every(check => ['pass', 'not_applicable'].includes(check)) ? 'passed' : 'pending';
      snapshot.human_status = [snapshot.visual_status, snapshot.powerpoint_status].includes('needs_changes') ? 'needs_changes' : snapshot.visual_status === 'passed' && snapshot.powerpoint_status === 'passed' ? 'passed' : 'pending';
      snapshot.pending_reasons = ['independent_postflight_integration_pending'];
      if (snapshot.acceptance_status === 'stale') snapshot.pending_reasons.push('source_stale');
      if (snapshot.visual_status !== 'passed') snapshot.pending_reasons.push(`visual_review_${snapshot.visual_status}`);
      if (snapshot.powerpoint_status !== 'passed') snapshot.pending_reasons.push(`powerpoint_review_${snapshot.powerpoint_status}`);
      snapshot.can_revoke = snapshot.records.some(record => record.kind !== 'revoke' && !record.revoked);
      return structuredClone(snapshot);
    },
  };
}

function Demo() {
  const [version, setVersion] = useState('demo-v1'); const [stale, setStale] = useState(false);
  const client = useMemo(() => memoryClient(stale), [stale]);
  return <><aside className="demo-banner"><h1>逐页预览与验收 · 交互演示</h1><p>此页面全部为合成数据。保存、撤销和状态均由本标签页内存模拟；刷新即清空，不写入服务器，不代表实际验收通过。</p><div className="demo-controls"><label>演示版本<select value={version} onChange={event => setVersion(event.target.value)}><option value="demo-v1">版本一</option><option value="demo-v2">版本二</option></select></label><button type="button" onClick={() => setStale(value => !value)}>{stale ? '恢复可检查示例（清空记录）' : '查看来源失效示例（清空记录）'}</button></div></aside><AcceptancePanel key={String(stale)} projectId="demo-project" versionId={version} client={client} /></>;
}

let demoRoot: Root | null = null;
let disposed = false;
let finishControllerWait: (() => void) | null = null;
const hot = (import.meta as ImportMeta & { hot?: { dispose(callback: () => void): void } }).hot;
hot?.dispose(() => {
  disposed = true;
  finishControllerWait?.();
  demoRoot?.unmount();
  demoRoot = null;
});

async function start() {
  const host = document.getElementById('demo-root')!;
  try {
    if (!('serviceWorker' in navigator)) throw Error('requires service workers');
    await navigator.serviceWorker.register('./demo-worker.js', { scope: './' });
    if (disposed) return;
    await navigator.serviceWorker.ready;
    if (disposed) return;
    if (!navigator.serviceWorker.controller) await new Promise<void>(resolve => {
      const finish = () => { navigator.serviceWorker.removeEventListener('controllerchange', finish); finishControllerWait = null; resolve(); };
      finishControllerWait = finish;
      navigator.serviceWorker.addEventListener('controllerchange', finish, { once: true });
    });
    if (disposed) return;
    demoRoot = createRoot(host);
    demoRoot.render(<Demo />);
  } catch { if (!disposed) host.textContent = '演示预览未能启动，请通过 localhost 或 HTTPS 打开此页面。'; }
}
void start();
