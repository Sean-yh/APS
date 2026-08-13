'use client';

import { useCallback } from 'react';
import ReactMarkdown from 'react-markdown';
import { ProgressCard } from './ToolCallCard';
import { FormCard } from './FormCard';
import { SchedulePreviewCard } from './SchedulePreviewCard';
import { ErpExportCard } from './ErpExportCard';
import type { Message, FormCard as FormCardType } from '@/types/chat';

interface MessageItemProps {
  message: Message;
  onFormCardUpdate?: (cardId: string, updates: Partial<FormCardType>) => void;
}

export function MessageItem({ message, onFormCardUpdate }: MessageItemProps) {
  const isUser = message.role === 'user';
  const hasToolCalls = !!message.toolCalls && message.toolCalls.length > 0;
  const allToolCallsCompleted =
    hasToolCalls && message.toolCalls!.every(t => t.output !== undefined);
  const shouldShowProgressCard =
    hasToolCalls && !(allToolCallsCompleted && !message.isStreaming);
  const hasFormCards = !!message.formCards && message.formCards.length > 0;
  const hasScheduleCards = !!message.scheduleCards && message.scheduleCards.length > 0;
  const hasErpExportCards = !!message.erpExportCards && message.erpExportCards.length > 0;

  // Handle form card submit
  const handleFormSubmit = useCallback(async (cardId: string, data: FormCardType['data']) => {
    if (onFormCardUpdate) {
      onFormCardUpdate(cardId, { status: 'submitted', data });
    }
  }, [onFormCardUpdate]);

  // Handle form card cancel
  const handleFormCancel = useCallback((cardId: string) => {
    if (onFormCardUpdate) {
      onFormCardUpdate(cardId, { status: 'cancelled' });
    }
  }, [onFormCardUpdate]);

  return (
    <div className={`animate-fade-in ${isUser ? 'flex justify-end' : ''}`}>
      {isUser ? (
        /* User Message - With Bubble */
        <div className="max-w-[85%]">
          <div className="inline-block rounded-2xl px-4 py-3 bg-[var(--user-bubble)] text-[var(--text-primary)]">
            {/* Render user content as markdown so things like **bold** are shown correctly. */}
            <ReactMarkdown
              components={{
                p: ({ children }) => (
                  <p className="whitespace-pre-wrap text-sm leading-relaxed my-0">{children}</p>
                ),
                strong: ({ children }) => <strong className="font-semibold">{children}</strong>,
              }}
            >
              {message.content}
            </ReactMarkdown>
          </div>
        </div>
      ) : (
        /* Assistant Message - No Bubble, Claude Style */
        <div className="max-w-full">
          {/* Main Content */}
          <div className="prose max-w-none text-sm leading-relaxed text-[var(--text-primary)]">
            <ReactMarkdown
              components={{
                code: ({ className, children, ...props }) => {
                  const isInline = !className;
                  return isInline ? (
                    <code
                      className="bg-[rgba(0,0,0,0.06)] rounded px-1.5 py-0.5 text-sm font-mono"
                      {...props}
                    >
                      {children}
                    </code>
                  ) : (
                    <code className={className} {...props}>
                      {children}
                    </code>
                  );
                },
                table: ({ children }) => (
                  <div className="overflow-x-auto my-3 rounded-lg border border-[var(--border-primary)]">
                    <table className="min-w-full text-sm">{children}</table>
                  </div>
                ),
                th: ({ children }) => (
                  <th className="border-b border-[var(--border-primary)] px-3 py-2 bg-[var(--bg-tertiary)] font-medium text-left text-[var(--text-primary)]">
                    {children}
                  </th>
                ),
                td: ({ children }) => (
                  <td className="border-b border-[var(--border-primary)] px-3 py-2 text-[var(--text-secondary)]">
                    {children}
                  </td>
                ),
                ul: ({ children }) => (
                  <ul className="list-disc list-outside pl-5 my-1 space-y-0.5 text-[var(--text-secondary)]">
                    {children}
                  </ul>
                ),
                ol: ({ children }) => (
                  <ol className="list-decimal list-outside pl-5 my-1 space-y-0.5 text-[var(--text-secondary)]">
                    {children}
                  </ol>
                ),
                li: ({ children }) => {
                  // ReactMarkdown can emit empty list items for stray "-" lines; don't render those bullets.
                  if (
                    children == null ||
                    (typeof children === 'string' && children.trim() === '') ||
                    (Array.isArray(children) &&
                      children.every((c) => typeof c === 'string' && c.trim() === ''))
                  ) {
                    return null;
                  }
                  return <li className="text-sm leading-snug">{children}</li>;
                },
                p: ({ children }) => (
                  <p className="my-1 leading-[1.6] text-[var(--text-primary)]">{children}</p>
                ),
                h2: ({ children }) => (
                  <h2 className="text-base font-semibold mt-3 mb-1.5 text-[var(--text-primary)]">
                    {children}
                  </h2>
                ),
                h3: ({ children }) => (
                  <h3 className="text-sm font-semibold mt-2.5 mb-1 text-[var(--text-primary)]">
                    {children}
                  </h3>
                ),
                a: ({ children, href }) => (
                  <a
                    href={href}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="text-[var(--accent-primary)] hover:underline"
                  >
                    {children}
                  </a>
                ),
                blockquote: ({ children }) => (
                  <blockquote className="border-l-3 border-[var(--border-primary)] pl-4 my-1.5 text-[var(--text-secondary)] italic">
                    {children}
                  </blockquote>
                ),
              }}
            >
              {message.content || (message.isStreaming ? '' : '')}
            </ReactMarkdown>

            {/* Thinking indicator */}
            {message.isStreaming && !message.content && (
              <span className="text-[var(--text-tertiary)]">思考中...</span>
            )}
          </div>

          {/* Tool Calls - Progress Style Cards */}
          {shouldShowProgressCard && (
            <div className="mt-3">
              <ProgressCard toolCalls={message.toolCalls!} isStreaming={message.isStreaming} />
            </div>
          )}

          {/* Form Cards */}
          {hasFormCards && message.formCards!.map((card) => (
            <FormCard
              key={card.id}
              card={card}
              onSubmit={handleFormSubmit}
              onCancel={handleFormCancel}
            />
          ))}

          {/* Schedule Preview Cards */}
          {hasScheduleCards && message.scheduleCards!.map((card) => (
            <SchedulePreviewCard key={card.id} card={card} />
          ))}

          {/* ERP Export Cards */}
          {hasErpExportCards && message.erpExportCards!.map((card) => (
            <ErpExportCard key={card.id} card={card} />
          ))}

          {/* Streaming indicator */}
          {message.isStreaming && message.content && (
            <div className="mt-2 flex items-center gap-1">
              <div className="flex gap-1">
                <span
                  className="w-1.5 h-1.5 bg-[var(--accent-primary)] rounded-full animate-bounce-dot"
                  style={{ animationDelay: '0ms' }}
                />
                <span
                  className="w-1.5 h-1.5 bg-[var(--accent-primary)] rounded-full animate-bounce-dot"
                  style={{ animationDelay: '150ms' }}
                />
                <span
                  className="w-1.5 h-1.5 bg-[var(--accent-primary)] rounded-full animate-bounce-dot"
                  style={{ animationDelay: '300ms' }}
                />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
