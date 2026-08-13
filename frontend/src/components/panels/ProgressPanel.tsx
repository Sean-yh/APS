'use client';

import { useChatStore } from '@/stores/chatStore';

interface ProgressItem {
  id: string;
  label: string;
  completed: boolean;
}

export function ProgressPanel() {
  const { activeSession } = useChatStore();

  const getProgressItems = (): ProgressItem[] => {
    if (!activeSession) {
      return [];
    }

    const items: ProgressItem[] = [];
    const messages = activeSession.messages;

    if (messages.length > 0) {
      items.push({ id: '1', label: '开始对话', completed: true });
    }

    const hasToolCalls = messages.some(m => m.toolCalls && m.toolCalls.length > 0);
    if (hasToolCalls) {
      items.push({ id: '2', label: '执行工具调用', completed: true });
    }

    const hasScheduleCall = messages.some(m =>
      m.toolCalls?.some(t => t.name.includes('schedule') || t.name.includes('排产'))
    );
    if (hasScheduleCall) {
      items.push({ id: '3', label: '生成排产方案', completed: true });
    }

    return items;
  };

  const items = getProgressItems();

  if (items.length === 0) {
    return (
      <div className="text-sm text-[var(--text-tertiary)] py-2">
        开始对话以查看进度
      </div>
    );
  }

  return (
    <div className="space-y-2">
      {items.map((item) => (
        <div key={item.id} className="flex items-center gap-2">
          <div
            className={`w-4 h-4 rounded border flex items-center justify-center ${
              item.completed
                ? 'bg-[var(--accent-primary)] border-[var(--accent-primary)]'
                : 'border-[var(--border-primary)]'
            }`}
          >
            {item.completed && (
              <svg className="w-3 h-3 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" />
              </svg>
            )}
          </div>
          <span className={`text-sm ${item.completed ? 'text-[var(--text-primary)]' : 'text-[var(--text-tertiary)]'}`}>
            {item.label}
          </span>
        </div>
      ))}
    </div>
  );
}
