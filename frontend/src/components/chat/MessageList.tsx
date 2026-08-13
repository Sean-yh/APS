'use client';

import { useRef, useEffect, useCallback, useState } from 'react';
import type { Message, FormCard } from '@/types/chat';
import { MessageItem } from './MessageItem';
import { WelcomeScreen } from './WelcomeScreen';

interface MessageListProps {
  messages: Message[];
  onSuggestionClick?: (suggestion: string) => void;
  onFormCardUpdate?: (messageId: string, cardId: string, updates: Partial<FormCard>) => void;
}

export function MessageList({ messages, onSuggestionClick, onFormCardUpdate }: MessageListProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const [stickToBottom, setStickToBottom] = useState(true);

  // Track whether the user is at (or near) the bottom. If they scroll up to select/copy
  // text, we should not keep yanking the viewport back down on every streamed token.
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;

    const thresholdPx = 80;
    const update = () => {
      const remaining = el.scrollHeight - el.scrollTop - el.clientHeight;
      setStickToBottom(remaining <= thresholdPx);
    };

    update();
    el.addEventListener('scroll', update, { passive: true });
    return () => el.removeEventListener('scroll', update);
  }, []);

  // Auto-scroll to bottom when new messages arrive
  useEffect(() => {
    if (!stickToBottom) return;
    // If the user currently has a text selection, don't disrupt it.
    const sel = typeof window !== 'undefined' ? window.getSelection?.() : null;
    if (sel && !sel.isCollapsed) return;
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, stickToBottom]);

  // Create a handler that binds the message ID
  const createFormCardHandler = useCallback(
    (messageId: string) => (cardId: string, updates: Partial<FormCard>) => {
      if (onFormCardUpdate) {
        onFormCardUpdate(messageId, cardId, updates);
      }
    },
    [onFormCardUpdate]
  );

  if (messages.length === 0) {
    return <WelcomeScreen onSuggestionClick={onSuggestionClick} />;
  }

  return (
    <div ref={containerRef} className="flex-1 overflow-y-auto">
      <div className="max-w-3xl mx-auto px-4 py-6 space-y-6">
        {messages.map((message) => (
          <MessageItem
            key={message.id}
            message={message}
            onFormCardUpdate={createFormCardHandler(message.id)}
          />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
