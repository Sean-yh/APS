'use client';

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConstraintsPanel } from './ConstraintsPanel';

function mockOkStatusFetch() {
  const status = {
    timestamp: '2026-01-25T19:06:36.030716Z',
    erp_snapshot: {
      orders: { path: 'orders.json', exists: true, timestamp: '2026-01-25T07:57:30Z', count: 249 },
      inventory: { path: 'inv.json', exists: true, timestamp: '2026-01-25T07:57:42Z', count: 16 },
    },
    overrides: { containers: ['EGC515529'], orders: [] },
    downtime_calendar: { holidays: [], maintenance: [] },
    production_context: {
      confirmed: true,
      checked_at: '2026-01-25T18:32:41.093870Z',
      forming_states: {
        'ROTARY-1': 'producing:S18G9C',
        'ROTARY-2': 'producing:S12G9W',
        'ROTARY-3': 'producing:S12G8Q',
      },
    },
    current_schedule: {
      exists: true,
      meta: {
        applied_constraints: {
          source_schedule_id: 'comparison_2',
          label: 'ALL',
          constraint: { type: 'full_reschedule' },
        },
      },
      applied_downtime: false,
      downtime_block_counts: { holiday: 0, maintenance: 0 },
    },
    comparisons: { count: 0, schedules: [] },
  };

  return vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue({ ok: true, json: async () => status } as Response);
}

describe('ConstraintsPanel', () => {
  it('defaults to manager overview and keeps timestamps clean', async () => {
    const fetchSpy = mockOkStatusFetch();
    const user = userEvent.setup();

    render(<ConstraintsPanel apiUrl="http://example.test" />);

    // Default view is manager-friendly overview.
    expect(await screen.findByRole('button', { name: /概览/ })).toHaveAttribute('aria-pressed', 'true');
    expect(screen.getByRole('button', { name: /详情/ })).toHaveAttribute('aria-pressed', 'false');

    expect(screen.getByText('ERP 快照')).toBeInTheDocument();
    expect(screen.getByText('249 订单 · 16 库存')).toBeInTheDocument();
    expect(screen.getByText('生产上下文')).toBeInTheDocument();

    // Details are still available via expansion.
    await user.click(screen.getByRole('button', { name: /生产上下文/i }));
    expect(screen.getAllByText('2026-01-25 18:32:41').length).toBeGreaterThan(0);
    expect(screen.queryByText(/\b0s\b/i)).not.toBeInTheDocument();

    fetchSpy.mockRestore();
  });

  it('shows override updated timestamp when expanded', async () => {
    const fetchSpy = mockOkStatusFetch();
    const user = userEvent.setup();

    render(<ConstraintsPanel apiUrl="http://example.test" />);

    expect(await screen.findByText('人工覆盖')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /人工覆盖/i }));
    expect(screen.getByText('更新于')).toBeInTheDocument();
    expect(screen.getByText('2026-01-25 19:06:36')).toBeInTheDocument();

    fetchSpy.mockRestore();
  });

  it('hides verbose downtime details until expanded in overview', async () => {
    const fetchSpy = mockOkStatusFetch();
    const user = userEvent.setup();

    render(<ConstraintsPanel apiUrl="http://example.test" />);

    // New UX: keep verbose lines hidden until the section is expanded.
    expect(await screen.findByText('停机日历')).toBeInTheDocument();
    expect(screen.queryByText(/当前甘特图已应用/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /停机日历/i }));
    expect(screen.getByText(/当前甘特图已应用/i)).toBeInTheDocument();

    fetchSpy.mockRestore();
  });

  it('hides raw applied constraints JSON until requested', async () => {
    const fetchSpy = mockOkStatusFetch();
    const user = userEvent.setup();

    render(<ConstraintsPanel apiUrl="http://example.test" />);

    // New UX: show a readable summary and hide raw JSON behind a toggle.
    expect(await screen.findByText('强制约束集')).toBeInTheDocument();
    expect(screen.queryByText(/source_schedule_id/i)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /强制约束集/i }));
    await user.click(screen.getByRole('button', { name: /查看原始 json/i }));
    expect(screen.getByText(/source_schedule_id/i)).toBeInTheDocument();

    fetchSpy.mockRestore();
  });

  it('switches to planner details view and expands sections by default', async () => {
    const fetchSpy = mockOkStatusFetch();
    const user = userEvent.setup();

    render(<ConstraintsPanel apiUrl="http://example.test" />);

    expect(await screen.findByText('ERP 快照')).toBeInTheDocument();
    expect(screen.queryByText(/2026-01-25 07:57:30/)).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /详情/ }));
    expect(screen.getByRole('button', { name: /详情/ })).toHaveAttribute('aria-pressed', 'true');

    // In details view, sections are expanded so timestamps are visible without extra clicks.
    expect(await screen.findByText(/2026-01-25 07:57:30/)).toBeInTheDocument();

    fetchSpy.mockRestore();
  });
});
