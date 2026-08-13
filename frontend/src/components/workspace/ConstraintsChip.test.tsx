'use client';

import { describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ConstraintsChip } from './ConstraintsChip';

function mockOkStatusFetch() {
  const status = {
    timestamp: '2026-01-01T00:00:00Z',
    erp_snapshot: {
      orders: { path: 'orders.json', exists: false },
      inventory: { path: 'inventory.json', exists: false },
    },
    overrides: { containers: ['C1', 'C2'], orders: ['O1'] },
    downtime_calendar: { holidays: [], maintenance: [] },
    production_context: { confirmed: true },
    current_schedule: {
      exists: true,
      meta: {},
      applied_downtime: true,
      downtime_block_counts: { holiday: 0, maintenance: 0 },
    },
    comparisons: { count: 0, schedules: [] },
  };

  return vi
    .spyOn(globalThis, 'fetch')
    .mockResolvedValue({ ok: true, json: async () => status } as Response);
}

describe('ConstraintsChip', () => {
  it('shows the number of constraints on the chip', async () => {
    const fetchSpy = mockOkStatusFetch();

    render(<ConstraintsChip apiUrl="http://example.test" />);

    // 2 containers + 1 order = 3 constraints
    expect(await screen.findByTestId('constraints-count')).toHaveTextContent('3');

    fetchSpy.mockRestore();
  });

  it('opens the drawer on click and closes on Escape', async () => {
    const fetchSpy = mockOkStatusFetch();
    const user = userEvent.setup();

    render(<ConstraintsChip apiUrl="http://example.test" />);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /排程依据/i }));
    expect(screen.getByRole('dialog', { name: /排程依据/i })).toBeInTheDocument();

    await user.keyboard('{Escape}');
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fetchSpy.mockRestore();
  });

  it('closes the drawer when clicking the backdrop', async () => {
    const fetchSpy = mockOkStatusFetch();
    const user = userEvent.setup();

    render(<ConstraintsChip apiUrl="http://example.test" />);

    await user.click(screen.getByRole('button', { name: /排程依据/i }));
    expect(screen.getByRole('dialog', { name: /排程依据/i })).toBeInTheDocument();

    await user.click(screen.getByTestId('constraints-drawer-backdrop'));
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();

    fetchSpy.mockRestore();
  });
});
