'use client';

import { describe, expect, it, vi } from 'vitest';
import { render } from '@testing-library/react';
import { MessageList } from './MessageList';

function msg(id: string, content: string) {
  return { id, role: 'assistant' as const, content, timestamp: Date.now() };
}

describe('MessageList', () => {
  it('auto-scrolls when messages change and there is no active selection', () => {
    // jsdom doesn't implement scrollIntoView by default.
    if (!Element.prototype.scrollIntoView) {
      Element.prototype.scrollIntoView = () => {};
    }
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {});
    vi.spyOn(window, 'getSelection').mockReturnValue({ isCollapsed: true } as unknown as Selection);

    const { rerender } = render(<MessageList messages={[msg('1', 'A')]} />);
    rerender(<MessageList messages={[msg('1', 'A'), msg('2', 'B')]} />);

    expect(scrollSpy).toHaveBeenCalled();
    scrollSpy.mockRestore();
  });

  it('does not auto-scroll while the user has an active text selection', () => {
    if (!Element.prototype.scrollIntoView) {
      Element.prototype.scrollIntoView = () => {};
    }
    const scrollSpy = vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(() => {});
    vi.spyOn(window, 'getSelection').mockReturnValue({ isCollapsed: false } as unknown as Selection);

    const { rerender } = render(<MessageList messages={[msg('1', 'A')]} />);
    rerender(<MessageList messages={[msg('1', 'A'), msg('2', 'B')]} />);

    expect(scrollSpy).not.toHaveBeenCalled();
    scrollSpy.mockRestore();
  });
});
