import { render, screen } from '@testing-library/react';
import { MessageItem } from './MessageItem';
import type { Message } from '@/types/chat';

describe('MessageItem', () => {
  it('renders markdown bold in user messages', () => {
    const message: Message = {
      id: 'm1',
      role: 'user',
      content: 'hello **bold**',
      timestamp: Date.now(),
    };

    render(<MessageItem message={message} />);

    const boldEl = screen.getByText('bold');
    expect(boldEl.tagName).toBe('STRONG');
  });
});

