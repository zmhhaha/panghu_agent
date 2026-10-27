import { afterEach, describe, expect, it, vi } from 'vitest';
import { api } from '../api';
import pendingFixture from '../../../server/acceptance/contracts/pending.fixture.json';
import passedFixture from '../../../server/acceptance/contracts/human-passed.fixture.json';
import visualFixture from '../../../server/acceptance/contracts/visual-review.fixture.json';
import powerpointFixture from '../../../server/acceptance/contracts/powerpoint-review.fixture.json';
import revokeFixture from '../../../server/acceptance/contracts/revoke.fixture.json';
import { acceptanceClient, assertSnapshot } from './client';
import type { AcceptanceCommand, AcceptanceSnapshot } from './contracts';
import { powerpointDraft, visualDraft } from './drafts';

afterEach(() => vi.unstubAllGlobals());

describe('P4 shared Python/TypeScript contract', () => {
  it('accepts actual backend-generated pending and human-passed snapshots while publication stays unauthorized', () => {
    const pending = assertSnapshot(pendingFixture as AcceptanceSnapshot, 'contract-project', 'contract-version');
    const passed = assertSnapshot(passedFixture as AcceptanceSnapshot, 'contract-project', 'contract-version');
    expect(visualDraft(pending).pages[0].verdict).toBe('pending');
    expect(visualDraft(passed).pages[0].verdict).toBe('pass');
    expect(powerpointDraft(passed).checks.reopened).toBe('pass');
    expect(passed.human_status).toBe('passed'); expect(passed.acceptance_status).toBe('pending'); expect(passed.publication_authorized).toBe(false);
  });

  it('keeps an explicitly stale old desktop record readable while refusing to restore its missing content-edit conclusion', () => {
    const legacy = structuredClone(passedFixture) as AcceptanceSnapshot;
    const record = legacy.records.find(item => item.kind === 'powerpoint_review')!; record.stale = true;
    delete (record.payload as { checks?: Record<string, string> }).checks!.content_edited;
    legacy.powerpoint_status = 'pending'; legacy.human_status = 'pending';
    assertSnapshot(legacy, 'contract-project', 'contract-version');
    expect(powerpointDraft(legacy).checks.content_edited).toBe('pending');
    record.stale = false;
    expect(() => assertSnapshot(legacy, 'contract-project', 'contract-version')).toThrow();
  });

  it('uses authenticated same-origin GET and existing mutation helper with CSRF and retry idempotency for all commands', async () => {
    const fetcher = vi.fn().mockImplementation(async (url: string) => new Response(JSON.stringify(url.endsWith('/me') ? { sub: 'reviewer', csrf_token: 'synthetic-csrf-token' } : pendingFixture)));
    vi.stubGlobal('fetch', fetcher); await api.me();
    const controller = new AbortController(); await acceptanceClient.load('contract-project', 'contract-version', controller.signal);
    const route = '/api/v1/projects/contract-project/versions/contract-version/acceptance';
    expect(fetcher.mock.calls[1][0]).toBe(route);
    const get = fetcher.mock.calls[1][1] as RequestInit;
    expect(get.credentials).toBe('same-origin'); expect(get.signal?.aborted).toBe(false); expect(new Headers(get.headers).has('X-CSRF-Token')).toBe(false);
    const command = visualFixture as AcceptanceCommand;
    fetcher.mockRejectedValueOnce(Error('transport interrupted'));
    await expect(acceptanceClient.save('contract-project', 'contract-version', command)).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
    const failedKey = new Headers((fetcher.mock.calls[2][1] as RequestInit).headers).get('Idempotency-Key');
    await acceptanceClient.save('contract-project', 'contract-version', command);
    const retry = fetcher.mock.calls[3][1] as RequestInit;
    expect(new Headers(retry.headers).get('Idempotency-Key')).toBe(failedKey);
    for (const fixture of [powerpointFixture, revokeFixture]) await acceptanceClient.save('contract-project', 'contract-version', fixture as AcceptanceCommand);
    for (const [url, init] of fetcher.mock.calls.slice(2) as [string, RequestInit][]) {
      expect(url).toBe(route + '/records'); expect(init.method).toBe('POST'); expect(init.credentials).toBe('same-origin');
      expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('synthetic-csrf-token');
      expect(new Headers(init.headers).get('Idempotency-Key')).toBeTruthy();
      expect(JSON.parse(String(init.body)).binding.source_revision).toBe(3);
    }
    expect(localStorage.length).toBe(0);
  });

  it('propagates caller cancellation to the pending authenticated fetch without retrying', async () => {
    const aborted = vi.fn();
    const fetcher = vi.fn((_url: string, init: RequestInit) => new Promise<Response>((_resolve, reject) => {
      init.signal!.addEventListener('abort', () => {
        aborted();
        reject(new DOMException('The operation was aborted.', 'AbortError'));
      }, { once: true });
    }));
    vi.stubGlobal('fetch', fetcher);
    const controller = new AbortController();
    const pending = acceptanceClient.load('contract-project', 'contract-version', controller.signal);
    const cancelled = expect(pending).rejects.toMatchObject({ code: 'REQUEST_CANCELLED' });
    expect(fetcher).toHaveBeenCalledOnce();
    const [url, init] = fetcher.mock.calls[0];
    expect(url).toBe('/api/v1/projects/contract-project/versions/contract-version/acceptance');
    expect(init.credentials).toBe('same-origin');
    expect(new Headers(init.headers).has('X-CSRF-Token')).toBe(false);
    expect(init.signal?.aborted).toBe(false);
    controller.abort();
    expect(init.signal?.aborted).toBe(true);
    expect(aborted).toHaveBeenCalledOnce();
    await cancelled;
    expect(fetcher).toHaveBeenCalledOnce();
  });
});
