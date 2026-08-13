'use client';

import { ReactNode, useState } from 'react';

interface ThreeColumnLayoutProps {
  leftPanel: ReactNode;
  chatArea: ReactNode;
  workspaceArea: ReactNode;
  header?: ReactNode;
  /** 是否显示工作区（右侧面板） */
  workspaceVisible?: boolean;
}

export function ThreeColumnLayout({
  leftPanel,
  chatArea,
  workspaceArea,
  header,
  workspaceVisible = true,
}: ThreeColumnLayoutProps) {
  const [isHistoryCollapsed, setIsHistoryCollapsed] = useState(false);

  return (
    <div className="flex flex-col h-screen overflow-hidden bg-[var(--bg-primary)]">
      {/* Header */}
      {header && (
        <header className="h-12 flex items-center justify-between px-4 border-b border-[var(--border-primary)] bg-[var(--bg-secondary)]">
          {header}
        </header>
      )}

      {/* Main Content - Three Columns */}
      <div className="flex flex-1 overflow-hidden">
        {/* Left Panel - History List (Collapsible) */}
        <aside
          className={`hidden md:flex flex-col border-r border-[var(--border-primary)] bg-[var(--bg-secondary)] transition-all duration-300 ${
            isHistoryCollapsed ? 'w-12' : 'w-[240px]'
          }`}
        >
          {/* Collapse/Expand Button */}
          <button
            onClick={() => setIsHistoryCollapsed(!isHistoryCollapsed)}
            className="h-10 flex items-center justify-center hover:bg-[var(--bg-hover)] transition-colors border-b border-[var(--border-primary)]"
            title={isHistoryCollapsed ? '展开历史' : '折叠历史'}
          >
            <svg
              className={`w-5 h-5 text-[var(--text-secondary)] transition-transform ${
                isHistoryCollapsed ? 'rotate-180' : ''
              }`}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M15 19l-7-7 7-7"
              />
            </svg>
          </button>

          {/* Panel Content */}
          <div
            className={`flex-1 overflow-hidden ${
              isHistoryCollapsed ? 'hidden' : 'block'
            }`}
          >
            {leftPanel}
          </div>
        </aside>

        {/* Middle Area - Chat */}
        <main className="flex-1 flex flex-col min-w-0 overflow-hidden">
          {chatArea}
        </main>

        {/* Right Area - Workspace (Gantt + Reserved) */}
        {workspaceVisible && (
          <aside className="hidden lg:flex flex-col flex-1 min-w-0 border-l border-[var(--border-primary)] bg-[var(--bg-primary)]">
            {workspaceArea}
          </aside>
        )}
      </div>
    </div>
  );
}
