'use client';

import { useState, useRef, useLayoutEffect, useEffect, useCallback } from 'react';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import type { GanttTaskSelectMessage, SelectedGanttTask } from '@/types/workspace';

interface ScheduleCardProps {
  /** 卡片标题 */
  title: string;
  /** 时间戳 */
  timestamp?: string;
  /** 甘特图 URL */
  ganttUrl: string;
  /** 是否为当前排产（不可删除） */
  isCurrent?: boolean;
  /** 删除回调 */
  onDelete?: () => void;
  /** 应用回调（将此方案应用为当前排产） */
  onApply?: () => void;
  /** 是否加载中 */
  loading?: boolean;
  /** 是否聚焦高亮（Canvas 风格） */
  isFocused?: boolean;
  /** 方案 ID，用于标识消息来源 */
  scheduleId: string;
  /** Gantt view window in days (Google Calendar style) */
  viewDays?: 1 | 3 | 5 | 7;
}

export function ScheduleCard({
  title,
  timestamp,
  ganttUrl,
  isCurrent = false,
  onDelete,
  onApply,
  loading = false,
  isFocused = false,
  scheduleId,
  viewDays = 7,
}: ScheduleCardProps) {
  // 聚焦时自动展开
  const [isCollapsed, setIsCollapsed] = useState(!isFocused);
  const [iframeKey, setIframeKey] = useState(0);
  const prevFocusedRef = useRef(isFocused);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const { selectTask, deselectTask, state } = useWorkspaceStore();

  // 当聚焦状态从 false 变为 true 时自动展开
  useLayoutEffect(() => {
    let rafId: number | null = null;
    if (isFocused && !prevFocusedRef.current) {
      // Avoid synchronous setState inside an effect body (eslint react-hooks/set-state-in-effect).
      rafId = requestAnimationFrame(() => setIsCollapsed(false));
    }
    prevFocusedRef.current = isFocused;
    return () => {
      if (rafId !== null) cancelAnimationFrame(rafId);
    };
  }, [isFocused]);

  // 监听来自 iframe 的 postMessage
  useEffect(() => {
    const handleMessage = (event: MessageEvent<GanttTaskSelectMessage>) => {
      // 验证消息来源是否是当前 iframe
      if (iframeRef.current && event.source === iframeRef.current.contentWindow) {
        const { type, payload } = event.data || {};

        if (type === 'gantt:task:select' && payload) {
          // 添加来源方案 ID
          const taskWithSource: SelectedGanttTask = {
            ...payload,
            sourceScheduleId: scheduleId,
          };
          selectTask(taskWithSource);
        } else if (type === 'gantt:task:deselect') {
          deselectTask();
        }
      }
    };

    window.addEventListener('message', handleMessage);
    return () => window.removeEventListener('message', handleMessage);
  }, [scheduleId, selectTask, deselectTask]);

  // 当选中任务来自其他方案时，通知当前 iframe 取消高亮
  useEffect(() => {
    if (state.selectedTask && state.selectedTask.sourceScheduleId !== scheduleId) {
      iframeRef.current?.contentWindow?.postMessage({
        type: 'gantt:clear-selection'
      }, '*');
    }
  }, [state.selectedTask, scheduleId]);

  // 格式化时间戳
  const formattedTime = timestamp
    ? new Date(timestamp).toLocaleString('zh-CN', {
        month: '2-digit',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
      })
    : '';

  // 刷新 iframe
  const handleRefresh = () => {
    setIframeKey((prev) => prev + 1);
  };

  // 新窗口打开甘特图
  const handleShare = () => {
    try {
      const url = new URL(ganttUrl);
      url.searchParams.set('viewDays', String(viewDays));
      url.searchParams.set('anchor', 'today');
      window.open(url.toString(), '_blank');
    } catch {
      window.open(ganttUrl, '_blank');
    }
  };

  const postViewToIframe = useCallback(() => {
    if (!iframeRef.current?.contentWindow) return;
    let targetOrigin = '*';
    try {
      targetOrigin = new URL(ganttUrl).origin;
    } catch {}

    iframeRef.current.contentWindow.postMessage(
      { type: 'gantt:set-view', payload: { viewDays, anchor: 'today' } },
      targetOrigin
    );
  }, [ganttUrl, viewDays]);

  // Sync view state into the embedded Gantt iframe without reloading it.
  useEffect(() => {
    postViewToIframe();
  }, [postViewToIframe]);

  // Give the Gantt iframe real vertical space when focused so all machine rows are visible.
  const ganttHeightClass = isFocused ? 'h-[calc(100dvh-16rem)]' : 'h-[420px]';

  return (
    <div
      className={`bg-[var(--bg-secondary)] rounded-lg border overflow-hidden flex flex-col transition-all ${
        isFocused
          ? 'border-[var(--accent-primary)] ring-2 ring-[var(--accent-primary)]/20'
          : 'border-[var(--border-primary)]'
      }`}
    >
      {/* 标题栏 */}
      <div className="flex items-center justify-between px-3 py-2 border-b border-[var(--border-primary)] bg-[var(--bg-tertiary)]">
        <div className="flex items-center gap-2 min-w-0">
          {/* 折叠按钮 */}
          <button
            onClick={() => setIsCollapsed(!isCollapsed)}
            className="p-1 rounded hover:bg-[var(--bg-hover)] transition-colors flex-shrink-0"
            title={isCollapsed ? '展开' : '折叠'}
          >
            <svg
              className={`w-4 h-4 text-[var(--text-secondary)] transition-transform ${
                isCollapsed ? '-rotate-90' : ''
              }`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M19 9l-7 7-7-7"
              />
            </svg>
          </button>

          {/* 标题和时间 */}
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-medium text-sm text-[var(--text-primary)] truncate">
              {title}
            </span>
            {formattedTime && (
              <span className="text-xs text-[var(--text-tertiary)] flex-shrink-0">
                {formattedTime}
              </span>
            )}
            {isCurrent && (
              <span className="text-xs px-1.5 py-0.5 rounded bg-[var(--accent-primary)] text-white flex-shrink-0">
                当前
              </span>
            )}
          </div>
        </div>

        {/* 操作按钮 */}
        <div className="flex items-center gap-1 flex-shrink-0">
          {/* 分享按钮 - 新开标签页 */}
          <button
            onClick={handleShare}
            className="p-1.5 rounded hover:bg-[var(--bg-hover)] transition-colors"
            title="新窗口打开"
          >
            <svg
              className="w-4 h-4 text-[var(--text-secondary)]"
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

          {/* 刷新按钮 */}
          <button
            onClick={handleRefresh}
            className="p-1.5 rounded hover:bg-[var(--bg-hover)] transition-colors"
            title="刷新"
            disabled={loading}
          >
            <svg
              className={`w-4 h-4 text-[var(--text-secondary)] ${loading ? 'animate-spin' : ''}`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
              />
            </svg>
          </button>

          {/* 应用按钮（仅对比方案显示） */}
          {!isCurrent && onApply && (
            <button
              onClick={onApply}
              className="p-1.5 rounded hover:bg-green-500/20 transition-colors"
              title="应用此方案"
              disabled={loading}
            >
              <svg
                className="w-4 h-4 text-green-500"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M5 13l4 4L19 7"
                />
              </svg>
            </button>
          )}

          {/* 删除按钮（仅对比方案显示） */}
          {!isCurrent && onDelete && (
            <button
              onClick={onDelete}
              className="p-1.5 rounded hover:bg-red-500/20 transition-colors"
              title="删除"
              disabled={loading}
            >
              <svg
                className="w-4 h-4 text-red-500"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"
                />
              </svg>
            </button>
          )}
        </div>
      </div>

      {/* 甘特图内容区 */}
      {!isCollapsed && (
        <div className={`${ganttHeightClass} min-h-[320px] relative`}>
          <iframe
            ref={iframeRef}
            key={iframeKey}
            src={ganttUrl}
            onLoad={() => {
              // Re-apply view after iframe reloads (e.g. refresh button).
              setTimeout(() => postViewToIframe(), 0);
            }}
            className="absolute inset-0 w-full h-full border-0"
            title={title}
          />
        </div>
      )}
    </div>
  );
}
