'use client';

import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { ScheduleCard } from '@/types/chat';

interface SchedulePreviewCardProps {
  card: ScheduleCard;
}

export function SchedulePreviewCard({ card }: SchedulePreviewCardProps) {
  const { openSchedule } = useWorkspaceStore();

  // 格式化时间戳
  const formattedTime = card.timestamp
    ? new Date(card.timestamp).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '';

  // 生成约束条件描述
  const constraintDesc = (() => {
    if (!card.constraint) return null;
    const parts: string[] = [];
    if (card.constraint.porefs?.length) {
      parts.push(`${card.constraint.porefs.length} 个柜`);
    }
    if (card.constraint.order_ids?.length) {
      parts.push(`${card.constraint.order_ids.length} 个订单`);
    }
    if (card.constraint.new_deadline) {
      parts.push(`截止 ${card.constraint.new_deadline}`);
    }
    if (card.constraint.priority_lock) {
      parts.push('优先级锁定');
    }
    return parts.length > 0 ? parts.join('，') : null;
  })();

  const handleOpen = () => {
    openSchedule(card.scheduleId);
  };

  return (
    <div className="mt-3 p-4 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] hover:border-[var(--accent-primary)] transition-colors">
      <div className="flex items-center justify-between">
        {/* 左侧：图标 + 标题 + 时间 */}
        <div className="flex items-center gap-3 min-w-0">
          {/* 甘特图图标 */}
          <div className="flex-shrink-0 w-10 h-10 rounded-lg bg-[var(--accent-primary)]/10 flex items-center justify-center">
            <svg
              className="w-5 h-5 text-[var(--accent-primary)]"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"
              />
            </svg>
          </div>

          {/* 标题和元信息 */}
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="font-medium text-[var(--text-primary)] truncate">
                {card.label}
              </span>
              {card.type === 'current' && (
                <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--accent-primary)] text-white flex-shrink-0">
                  当前
                </span>
              )}
            </div>
            <div className="flex items-center gap-2 mt-0.5">
              {formattedTime && (
                <span className="text-xs text-[var(--text-tertiary)]">
                  {formattedTime}
                </span>
              )}
              {constraintDesc && (
                <>
                  <span className="text-xs text-[var(--text-tertiary)]">·</span>
                  <span className="text-xs text-[var(--text-secondary)] truncate">
                    {constraintDesc}
                  </span>
                </>
              )}
            </div>
          </div>
        </div>

        {/* 右侧：Open 按钮 */}
        <button
          onClick={handleOpen}
          className="flex-shrink-0 ml-3 px-4 py-1.5 rounded-lg bg-[var(--accent-primary)] text-white text-sm font-medium hover:opacity-90 transition-opacity flex items-center gap-1.5"
        >
          <span>Open</span>
          <svg
            className="w-4 h-4"
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M10 6H6a2 2 0 00-2 2v10a2 2 0 002 2h10a2 2 0 002-2v-4M14 4h6m0 0v6m0-6L10 14"
            />
          </svg>
        </button>
      </div>
    </div>
  );
}
