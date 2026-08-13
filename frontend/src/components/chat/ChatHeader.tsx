'use client';

import { MenuIcon, TrashIcon } from '@/components/ui/Icons';

interface ChatHeaderProps {
  title?: string;
  onToggleSidebar?: () => void;
  onClearChat?: () => void;
  showMenuButton?: boolean;
}

export function ChatHeader({
  title = 'L2 排产 AI 助手',
  onToggleSidebar,
  onClearChat,
  showMenuButton = true,
}: ChatHeaderProps) {
  return (
    <header className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-primary)] bg-[var(--bg-primary)]">
      <div className="flex items-center gap-3">
        {showMenuButton && onToggleSidebar && (
          <button
            onClick={onToggleSidebar}
            className="p-2 -ml-2 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors md:hidden"
          >
            <MenuIcon className="w-5 h-5" />
          </button>
        )}
        <h1 className="text-base font-medium text-[var(--text-primary)]">{title}</h1>
      </div>

      <div className="flex items-center gap-2">
        {onClearChat && (
          <button
            onClick={onClearChat}
            className="p-2 rounded-lg hover:bg-[var(--bg-hover)] text-[var(--text-secondary)] hover:text-[var(--text-primary)] transition-colors"
            title="清空对话"
          >
            <TrashIcon className="w-5 h-5" />
          </button>
        )}
      </div>
    </header>
  );
}
