'use client';

import { useState } from 'react';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { ScheduleCard } from './ScheduleCard';
import { ConstraintsChip } from './ConstraintsChip';

interface WorkspaceAreaProps {
  apiUrl?: string;
}

export function WorkspaceArea({ apiUrl = 'http://localhost:8000' }: WorkspaceAreaProps) {
  const { state, deleteComparison, applyComparison, closeWorkspace } = useWorkspaceStore();
  const [viewDays, setViewDays] = useState<1 | 3 | 5 | 7>(7);

  // 无数据时不渲染
  if (!state.showWorkspace) {
    return null;
  }

  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* 标题栏（手动打开时显示关闭按钮） */}
      {state.isManuallyOpened && (
        <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-primary)] bg-[var(--bg-secondary)]">
          <h2 className="font-medium text-[var(--text-primary)]">排产方案</h2>
          <button
            onClick={closeWorkspace}
            className="p-1.5 rounded-lg hover:bg-[var(--bg-hover)] transition-colors"
            title="关闭"
          >
            <svg
              className="w-5 h-5 text-[var(--text-secondary)]"
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
      )}

      {/* 滚动容器 */}
      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* View controls (Google Calendar style day range) */}
        <div className="sticky top-0 z-10 -mt-4 pt-4 pb-2 bg-[var(--bg-primary)]">
          <div className="flex items-center justify-between gap-3">
            <div className="inline-flex rounded-lg border border-[var(--border-primary)] overflow-hidden bg-[var(--card-bg)]">
              {([1, 3, 5, 7] as const).map((d) => (
                <button
                  key={d}
                  type="button"
                  onClick={() => setViewDays(d)}
                  className={`px-2.5 py-1.5 text-sm font-medium transition-colors ${
                    viewDays === d
                      ? 'bg-[var(--accent-primary)] text-white'
                      : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)]'
                  }`}
                  aria-pressed={viewDays === d}
                >
                  {d}D
                </button>
              ))}
            </div>

            <ConstraintsChip apiUrl={apiUrl} />
          </div>
        </div>

        {/* 当前排产 */}
        {state.currentSchedule && (
          <ScheduleCard
            title="当前排产"
            timestamp={state.currentSchedule.timestamp}
            ganttUrl={state.currentSchedule.ganttUrl}
            isCurrent={true}
            loading={state.loading}
            isFocused={state.focusedScheduleId === 'current'}
            scheduleId="current"
            viewDays={viewDays}
          />
        )}

        {/* 重排方案列表 */}
        {state.comparisons.length > 0 && (
          <>
            {/* 分隔标题 */}
            <div className="flex items-center gap-2 pt-2">
              <div className="flex-1 h-px bg-[var(--border-primary)]" />
              <span className="text-xs text-[var(--text-tertiary)] px-2">
                重排方案 ({state.comparisons.length})
              </span>
              <div className="flex-1 h-px bg-[var(--border-primary)]" />
            </div>

            {/* 方案卡片列表 */}
            {state.comparisons.map((comparison) => (
              <ScheduleCard
                key={comparison.id}
                title={comparison.label}
                timestamp={comparison.timestamp}
                ganttUrl={comparison.ganttUrl}
                onDelete={() => deleteComparison(comparison.id)}
                onApply={() => applyComparison(comparison.id)}
                loading={state.loading}
                isFocused={state.focusedScheduleId === comparison.id}
                scheduleId={comparison.id}
                viewDays={viewDays}
              />
            ))}
          </>
        )}

        {/* 空状态提示 */}
        {!state.currentSchedule && state.comparisons.length === 0 && (
          <div className="flex-1 flex items-center justify-center text-[var(--text-tertiary)]">
            <p>暂无排产数据</p>
          </div>
        )}
      </div>

      {/* 错误提示 */}
      {state.error && (
        <div className="px-4 py-2 bg-red-500/10 border-t border-red-500/20">
          <p className="text-sm text-red-500">{state.error}</p>
        </div>
      )}
    </div>
  );
}
