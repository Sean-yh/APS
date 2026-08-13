'use client';

import { useMemo } from 'react';
import { ChatListItem } from './ChatListItem';
import type { ChatSession } from '@/types/chat';

// Snapshot "now" once at module init so renders remain pure/idempotent (eslint react-hooks/purity).
const NOW_MS = Date.now();

interface ChatListProps {
  sessions: ChatSession[];
  activeSessionId: string | null;
  onSelectSession: (id: string) => void;
  onDeleteSession: (id: string) => void;
  collapsed?: boolean;
}

interface GroupedSessions {
  today: ChatSession[];
  yesterday: ChatSession[];
  lastWeek: ChatSession[];
  older: ChatSession[];
}

export function ChatList({
  sessions,
  activeSessionId,
  onSelectSession,
  onDeleteSession,
  collapsed = false,
}: ChatListProps) {
  // 按时间分组会话
  const groupedSessions = useMemo<GroupedSessions>(() => {
    const dayMs = 24 * 60 * 60 * 1000;
    const today = NOW_MS - dayMs;
    const yesterday = NOW_MS - 2 * dayMs;
    const lastWeek = NOW_MS - 7 * dayMs;

    const groups: GroupedSessions = {
      today: [],
      yesterday: [],
      lastWeek: [],
      older: [],
    };

    // 按 updatedAt 排序（最新的在前）
    const sorted = [...sessions].sort((a, b) => b.updatedAt - a.updatedAt);

    sorted.forEach((session) => {
      if (session.updatedAt > today) {
        groups.today.push(session);
      } else if (session.updatedAt > yesterday) {
        groups.yesterday.push(session);
      } else if (session.updatedAt > lastWeek) {
        groups.lastWeek.push(session);
      } else {
        groups.older.push(session);
      }
    });

    return groups;
  }, [sessions]);

  if (sessions.length === 0) {
    if (collapsed) return null;

    return (
      <div className="px-3 py-8 text-center">
        <p className="text-sm text-[var(--text-tertiary)]">暂无对话历史</p>
      </div>
    );
  }

  const renderGroup = (title: string, groupSessions: ChatSession[]) => {
    if (groupSessions.length === 0) return null;

    return (
      <div className="mb-4">
        {!collapsed && (
          <h3 className="px-3 py-2 text-xs font-medium text-[var(--text-tertiary)] uppercase tracking-wider">
            {title}
          </h3>
        )}
        <div className="space-y-1">
          {groupSessions.map((session) => (
            <ChatListItem
              key={session.id}
              session={session}
              isActive={session.id === activeSessionId}
              onClick={() => onSelectSession(session.id)}
              onDelete={() => onDeleteSession(session.id)}
              collapsed={collapsed}
            />
          ))}
        </div>
      </div>
    );
  };

  return (
    <div className="flex-1 overflow-y-auto px-2 py-2">
      {renderGroup('今天', groupedSessions.today)}
      {renderGroup('昨天', groupedSessions.yesterday)}
      {renderGroup('最近7天', groupedSessions.lastWeek)}
      {renderGroup('更早', groupedSessions.older)}
    </div>
  );
}
