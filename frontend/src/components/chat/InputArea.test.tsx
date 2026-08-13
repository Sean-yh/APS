'use client';

import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { InputArea } from './InputArea';

vi.mock('@/stores/workspaceStore', () => ({
  useWorkspaceStore: () => ({
    state: { selectedTask: null },
    deselectTask: vi.fn(),
  }),
}));

describe('InputArea', () => {
  it('auto-expands up to a max height and avoids showing a scrollbar for short input', async () => {
    render(
      <InputArea
        isLoading={false}
        onSend={() => {}}
        onStop={() => {}}
      />
    );

    const textarea = screen.getByPlaceholderText('输入消息...') as HTMLTextAreaElement;

    // jsdom doesn't compute layout; stub scrollHeight so the resize effect can run.
    let scrollHeight = 40;
    Object.defineProperty(textarea, 'scrollHeight', {
      configurable: true,
      get: () => scrollHeight,
    });

    fireEvent.change(textarea, { target: { value: 'hello' } });

    await waitFor(() => {
      expect(textarea.style.height).toBe('40px');
      expect(textarea.style.overflowY).toBe('hidden');
    });
  });

  it('caps auto-expand height and enables scrolling (without showing a scrollbar) for long input', async () => {
    render(
      <InputArea
        isLoading={false}
        onSend={() => {}}
        onStop={() => {}}
      />
    );

    const textarea = screen.getByPlaceholderText('输入消息...') as HTMLTextAreaElement;

    let scrollHeight = 500;
    Object.defineProperty(textarea, 'scrollHeight', {
      configurable: true,
      get: () => scrollHeight,
    });

    fireEvent.change(textarea, { target: { value: 'x\n'.repeat(200) } });

    await waitFor(() => {
      expect(textarea.style.height).toBe('200px');
      expect(textarea.style.overflowY).toBe('auto');
      expect(textarea.className).toContain('scrollbar-hide');
    });
  });
});

