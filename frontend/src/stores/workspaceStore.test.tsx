'use client';

import React from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { act, render } from '@testing-library/react';

import { WorkspaceProvider, useWorkspaceStore } from './workspaceStore';

function flushMicrotasks() {
  return act(async () => {
    // Two ticks is usually enough to resolve chained awaits (fetch -> json).
    await Promise.resolve();
    await Promise.resolve();
  });
}

describe('workspaceStore polling', () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('does not dispatch/re-render repeatedly when workspace status is unchanged', async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const u =
        typeof input === 'string'
          ? input
          : input instanceof URL
            ? input.toString()
            : input.url;
      if (u.endsWith('/api/schedule/comparison/status')) {
        return new Response(JSON.stringify({ schedules: [] }), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      if (u.endsWith('/api/schedule/gantt') && init?.method === 'HEAD') {
        return new Response(null, { status: 200, headers: {} });
      }
      throw new Error(`Unexpected fetch: ${u}`);
    }) satisfies typeof fetch;
    vi.stubGlobal('fetch', fetchMock);

    let commits = 0;
    const onCommit = () => {
      commits += 1;
    };

    function Counter({ onCommit }: { onCommit: () => void }) {
      // Access state to ensure we re-render on SYNC_STATE.
      const { state } = useWorkspaceStore();
      React.useEffect(() => {
        onCommit();
      });
      return <div>{state.currentSchedule?.id ?? 'none'}</div>;
    }

    const { unmount } = render(
      <WorkspaceProvider apiUrl="http://localhost:8000">
        <Counter onCommit={onCommit} />
      </WorkspaceProvider>
    );

    await flushMicrotasks();
    const commitsAfterInitialLoad = commits;

    // Advance through multiple polling ticks with unchanged backend responses.
    for (let i = 0; i < 3; i += 1) {
      await act(async () => {
        vi.advanceTimersByTime(3000);
      });
      await flushMicrotasks();
    }

    expect(commits).toBe(commitsAfterInitialLoad);

    unmount();
  });
});
