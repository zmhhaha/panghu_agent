import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError } from '../api';
import pendingFixture from '../../../server/acceptance/contracts/pending.fixture.json';
import passedFixture from '../../../server/acceptance/contracts/human-passed.fixture.json';
import visualFixture from '../../../server/acceptance/contracts/visual-review.fixture.json';
import powerpointFixture from '../../../server/acceptance/contracts/powerpoint-review.fixture.json';
import revokeFixture from '../../../server/acceptance/contracts/revoke.fixture.json';
import { acceptanceClient, assertSnapshot } from './client';
import type { AcceptanceCommand, AcceptanceSnapshot } from './contracts';
import { powerpointDraft, visualDraft } from './drafts';
import { syntheticLayoutFailure } from './fixtures';

afterEach(() => vi.unstubAllGlobals());

describe('P4 shared Python/TypeScript contract', () => {
  it('accepts bounded measured-failure diagnostics without promoting any human or publication result', () => {
    const failure = syntheticLayoutFailure();
    const value = assertSnapshot(failure, failure.project_id, failure.version_id);
    expect(value.programmatic?.diagnostics?.items[0]).toMatchObject({ page: 2, code: 'text_collision', shape_id: 17, other_shape_id: 22 });
    expect(value.visual_status).toBe('pending'); expect(value.powerpoint_status).toBe('pending'); expect(value.publication_authorized).toBe(false);
    value.programmatic!.diagnostics!.items[0].other_shape_id = null;
    assertSnapshot(value, value.project_id, value.version_id);
    delete value.programmatic!.diagnostics!.items[0].other_shape_id;
    assertSnapshot(value, value.project_id, value.version_id);
    value.programmatic!.diagnostics!.total_issue_count = 101; value.programmatic!.diagnostics!.truncated = true;
    assertSnapshot(value, value.project_id, value.version_id);
  });

  it.each(['unknown_code', 'prototype_code', 'page_zero', 'page_overflow', 'boolean_page', 'string_shape', 'boolean_shape',
    'other_self', 'message_injection', 'suggestion_url', 'title_injection', 'raw_source_path', 'count_overflow', 'count_zero',
    'truncation', 'too_many_items', 'no_final_gate', 'completed_gate', 'rendered_roster', 'no_receipt'])('rejects unsafe layout diagnostic %s', fault => {
    const value = syntheticLayoutFailure(); const programmatic = value.programmatic!;
    const diagnostic = programmatic.diagnostics!;
    const row = diagnostic.items[0] as unknown as Record<string, unknown>;
    if (fault === 'unknown_code') row.code = 'candidate_rule';
    else if (fault === 'prototype_code') row.code = '__proto__';
    else if (fault === 'page_zero') row.page = 0;
    else if (fault === 'page_overflow') row.page = value.pages.length + 1;
    else if (fault === 'boolean_page') row.page = true;
    else if (fault === 'string_shape') row.shape_id = '17';
    else if (fault === 'boolean_shape') row.shape_id = true;
    else if (fault === 'other_self') row.other_shape_id = row.shape_id;
    else if (fault === 'message_injection') row.message = '<img src=x onerror=alert(1)>';
    else if (fault === 'suggestion_url') row.suggestion = 'https://private.invalid/secret';
    else if (fault === 'title_injection') diagnostic.title = '<script>private source</script>';
    else if (fault === 'raw_source_path') row.source_path = '/private/customer/deck';
    else if (fault === 'count_overflow') diagnostic.total_issue_count = 1000001;
    else if (fault === 'count_zero') diagnostic.total_issue_count = 0;
    else if (fault === 'truncation') diagnostic.truncated = true;
    else if (fault === 'too_many_items') { diagnostic.items = Array.from({ length: 101 }, () => ({ ...diagnostic.items[0] })); diagnostic.total_issue_count = 101; }
    else if (fault === 'no_final_gate') programmatic.pending_gates = [];
    else if (fault === 'completed_gate') programmatic.completed_gates = ['independent_final_pptx_rendering'];
    else if (fault === 'rendered_roster') programmatic.rendered_pages = [{ number: 1, preview_sha256: '1'.repeat(64), preview_url: '/private/fake.png' }];
    else if (fault === 'no_receipt') programmatic.receipt_sha256 = null;
    expect(() => assertSnapshot(value, value.project_id, value.version_id)).toThrowError(new ApiError('INVALID_RESPONSE'));
  });

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

  it('uses the dedicated authenticated publication route with explicit confirmation and stable retry intent', async () => {
    const fetcher = vi.fn().mockImplementation(async (url: string) => new Response(JSON.stringify(url.endsWith('/me') ? { sub: 'reviewer', csrf_token: 'synthetic-csrf-token' } : pendingFixture)));
    vi.stubGlobal('fetch', fetcher); await api.me();
    const command = { kind: 'publish' as const, expected_revision: 2, binding: (pendingFixture as AcceptanceSnapshot).binding, confirm: true as const };
    fetcher.mockRejectedValueOnce(Error('transport interrupted'));
    await expect(acceptanceClient.publish!('contract-project', 'contract-version', command)).rejects.toMatchObject({ code: 'NETWORK_ERROR' });
    await acceptanceClient.publish!('contract-project', 'contract-version', command);
    const writes = fetcher.mock.calls.slice(1) as [string, RequestInit][];
    expect(writes).toHaveLength(2); const keys = writes.map(([, init]) => new Headers(init.headers).get('Idempotency-Key'));
    expect(keys[0]).toBeTruthy(); expect(keys[1]).toBe(keys[0]);
    for (const [url, init] of writes) {
      expect(url).toBe('/api/v1/projects/contract-project/versions/contract-version/acceptance/publication');
      expect(init.method).toBe('POST'); expect(init.credentials).toBe('same-origin');
      expect(new Headers(init.headers).get('X-CSRF-Token')).toBe('synthetic-csrf-token'); expect(JSON.parse(String(init.body))).toEqual(command);
    }
  });
});
