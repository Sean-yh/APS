'use client';

import { useState, useRef, useEffect, FormEvent } from 'react';
import { SendIcon, StopIcon } from '@/components/ui/Icons';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { SelectedGanttTask } from '@/types/workspace';

interface InputAreaProps {
  onSend: (message: string, context?: SelectedGanttTask) => void;
  onStop: () => void;
  isLoading: boolean;
  placeholder?: string;
}

// 格式化时间显示
function formatTime(isoStr: string) {
  try {
    return new Date(isoStr).toLocaleString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit',
    });
  } catch {
    return isoStr;
  }
}

// 获取任务类型的中文名称
function getTypeName(type: string) {
  const typeMap: Record<string, string> = {
    forming: '成型',
    label: '贴标',
    setup: '换色',
    idle: '空闲',
  };
  return typeMap[type] || type;
}

// 上下文卡片子组件
function TaskContextCard({
  task,
  onDismiss,
}: {
  task: SelectedGanttTask;
  onDismiss: () => void;
}) {
  return (
    <div className="mx-4 mb-2 px-3 py-2 bg-[var(--accent-primary)]/10 border border-[var(--accent-primary)]/30 rounded-xl flex items-center gap-3">
      {/* 任务图标 */}
      <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-[var(--accent-primary)]/20 flex items-center justify-center">
        <svg
          className="w-4 h-4 text-[var(--accent-primary)]"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
          />
        </svg>
      </div>

      {/* 任务信息 */}
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-medium text-[var(--text-primary)]">
            {task.sku || getTypeName(task.type)}
          </span>
          <span className="text-[var(--text-tertiary)]">|</span>
          <span className="text-[var(--text-secondary)]">{task.machine}</span>
          {task.orderId && (
            <>
              <span className="text-[var(--text-tertiary)]">|</span>
              <span className="text-[var(--text-secondary)]">
                订单 #{task.orderId}
              </span>
            </>
          )}
        </div>
        <div className="text-xs text-[var(--text-tertiary)] truncate">
          {formatTime(task.start)} - {formatTime(task.end)}
          {task.quantity && ` · ${task.quantity.toLocaleString()} 件`}
        </div>
      </div>

      {/* 关闭按钮 */}
      <button
        onClick={onDismiss}
        className="flex-shrink-0 p-1 rounded-md hover:bg-[var(--bg-hover)] transition-colors"
        title="取消选中"
      >
        <svg
          className="w-4 h-4 text-[var(--text-tertiary)]"
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M6 18L18 6M6 6l12 12"
          />
        </svg>
      </button>
    </div>
  );
}

export function InputArea({
  onSend,
  onStop,
  isLoading,
  placeholder = '输入消息...',
}: InputAreaProps) {
  const [input, setInput] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const { state, deselectTask } = useWorkspaceStore();

  const selectedTask = state.selectedTask;

  // Auto-resize textarea
  useEffect(() => {
    const textarea = textareaRef.current;
    if (textarea) {
      const maxHeight = 200; // Match ChatGPT/Claude: grow until a cap, then scroll internally.
      textarea.style.height = 'auto';
      const nextHeight = Math.min(textarea.scrollHeight, maxHeight);
      textarea.style.height = `${nextHeight}px`;
      // Prevent the "always visible" scrollbar when content fits.
      textarea.style.overflowY = textarea.scrollHeight > maxHeight ? 'auto' : 'hidden';
    }
  }, [input]);

  // Focus on mount
  useEffect(() => {
    textareaRef.current?.focus();
  }, []);

  // 当有选中任务时更新 placeholder
  const dynamicPlaceholder = selectedTask
    ? `针对 ${selectedTask.sku || getTypeName(selectedTask.type)} 任务输入操作...`
    : placeholder;

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (input.trim() && !isLoading) {
      // 发送消息时携带上下文
      onSend(input, selectedTask || undefined);
      setInput('');
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSubmit(e);
    }
    // ESC 键清除选中
    if (e.key === 'Escape' && selectedTask) {
      deselectTask();
    }
  };

  return (
    <div className="border-t border-[var(--border-primary)] bg-[var(--bg-primary)]">
      {/* 选中任务上下文卡片 */}
      {selectedTask && (
        <div className="pt-3">
          <TaskContextCard task={selectedTask} onDismiss={deselectTask} />
        </div>
      )}

      {/* 输入区域 */}
      <div className="px-4 py-4">
        <form onSubmit={handleSubmit} className="max-w-3xl mx-auto">
          <div className="relative flex items-end gap-3 bg-[var(--card-bg)] rounded-2xl border border-[var(--border-primary)] focus-within:border-[var(--accent-primary)] transition-colors shadow-sm">
            <textarea
              ref={textareaRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={dynamicPlaceholder}
              disabled={isLoading}
              rows={1}
              className="
                flex-1 resize-none bg-transparent
                px-4 py-3 pr-14
                text-[var(--text-primary)] placeholder-[var(--text-tertiary)]
                focus:outline-none
                disabled:opacity-50 disabled:cursor-not-allowed
                text-sm leading-relaxed
                scrollbar-hide
              "
            />

            {/* 发送/停止按钮 */}
            <div className="absolute right-2 bottom-2">
              {isLoading ? (
                <button
                  type="button"
                  onClick={onStop}
                  className="
                    p-2 rounded-lg
                    bg-red-100 text-red-600
                    hover:bg-red-200
                    transition-colors
                  "
                  title="停止生成"
                >
                  <StopIcon className="w-5 h-5" />
                </button>
              ) : (
                <button
                  type="submit"
                  disabled={!input.trim()}
                  className="
                    p-2 rounded-lg
                    bg-[var(--accent-primary)] text-white
                    hover:bg-[var(--accent-hover)]
                    disabled:opacity-30 disabled:cursor-not-allowed
                    transition-colors
                  "
                  title="发送消息"
                >
                  <SendIcon className="w-5 h-5" />
                </button>
              )}
            </div>
          </div>
        </form>
      </div>
    </div>
  );
}
