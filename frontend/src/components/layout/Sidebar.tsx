'use client';

import { useState } from 'react';
import { useChatStore } from '@/stores/chatStore';
import { NewChatButton } from './NewChatButton';
import { ChatList } from './ChatList';
import { ChevronLeftIcon, ChevronRightIcon, MenuIcon, XIcon } from '@/components/ui/Icons';

interface SidebarProps {
  className?: string;
}

export function Sidebar({ className = '' }: SidebarProps) {
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
  const {
    state,
    createSession,
    deleteSession,
    setActiveSession,
    toggleSidebar,
  } = useChatStore();

  const { sessions, activeSessionId, sidebarOpen } = state;

  // 移动端侧边栏单独的状态
  const [mobileOpen, setMobileOpen] = useState(false);

  // 关闭移动端侧边栏当点击背景或选择会话
  const closeMobileSidebar = () => setMobileOpen(false);

  const handleNewChat = () => {
    createSession();
    closeMobileSidebar();
  };

  return (
    <>
      {/* 桌面版侧边栏 */}
      <aside
        className={`
          hidden md:flex flex-col
          bg-[var(--bg-secondary)] border-r border-[var(--border-primary)]
          transition-sidebar h-full
          ${sidebarOpen ? 'w-64' : 'w-16'}
          ${className}
        `}
      >
        {/* 顶部区域 */}
        <div className="p-3 flex items-center gap-2">
          {sidebarOpen ? (
            <>
              <div className="flex-1">
                <NewChatButton onClick={() => createSession()} />
              </div>
              <button
                onClick={toggleSidebar}
                className="p-2 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                title="收起侧边栏"
              >
                <ChevronLeftIcon className="w-5 h-5" />
              </button>
            </>
          ) : (
            <div className="w-full flex flex-col gap-2 items-center">
              <button
                onClick={toggleSidebar}
                className="p-2 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
                title="展开侧边栏"
              >
                <ChevronRightIcon className="w-5 h-5" />
              </button>
              <NewChatButton onClick={() => createSession()} collapsed />
            </div>
          )}
        </div>

        {/* 聊天列表 */}
        <ChatList
          sessions={sessions}
          activeSessionId={activeSessionId}
          onSelectSession={setActiveSession}
          onDeleteSession={deleteSession}
          collapsed={!sidebarOpen}
        />

        {/* 底部区域 */}
        {sidebarOpen && (
          <div className="p-3 border-t border-[var(--border-primary)]">
            <a
              href={`${apiUrl}/api/schedule/gantt`}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] transition-colors"
            >
              <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
              </svg>
              查看甘特图
            </a>
          </div>
        )}
      </aside>

      {/* 移动版汉堡菜单按钮 */}
      <button
        onClick={() => setMobileOpen(true)}
        className="fixed md:hidden top-4 left-4 z-40 p-2 rounded-lg bg-[var(--bg-tertiary)] text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors shadow-lg"
      >
        <MenuIcon className="w-5 h-5" />
      </button>

      {/* 移动版侧边栏（抽屉） */}
      {mobileOpen && (
        <>
          {/* 背景遮罩 */}
          <div
            className="fixed md:hidden inset-0 bg-black/50 z-40"
            onClick={closeMobileSidebar}
          />

          {/* 抽屉侧边栏 */}
          <aside
            className="
              fixed md:hidden inset-y-0 left-0 z-50
              w-72 bg-[var(--bg-secondary)] border-r border-[var(--border-primary)]
              flex flex-col animate-slide-in-left
            "
          >
            {/* 顶部 */}
            <div className="p-3 flex items-center gap-2">
              <div className="flex-1">
                <NewChatButton onClick={handleNewChat} />
              </div>
              <button
                onClick={closeMobileSidebar}
                className="p-2 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
              >
                <XIcon className="w-5 h-5" />
              </button>
            </div>

            {/* 聊天列表 */}
            <ChatList
              sessions={sessions}
              activeSessionId={activeSessionId}
              onSelectSession={(id) => {
                setActiveSession(id);
                closeMobileSidebar();
              }}
              onDeleteSession={deleteSession}
            />

            {/* 底部 */}
            <div className="p-3 border-t border-[var(--border-primary)]">
              <a
                href={`${apiUrl}/api/schedule/gantt`}
                target="_blank"
                rel="noopener noreferrer"
                className="flex items-center gap-3 px-3 py-2 rounded-lg text-sm text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)] transition-colors"
              >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
                查看甘特图
              </a>
            </div>
          </aside>
        </>
      )}
    </>
  );
}
