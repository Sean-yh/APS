'use client';

import { PlusIcon } from '@/components/ui/Icons';

interface NewChatButtonProps {
  onClick: () => void;
  collapsed?: boolean;
}

export function NewChatButton({ onClick, collapsed = false }: NewChatButtonProps) {
  return (
    <button
      onClick={onClick}
      className={`
        flex items-center gap-3 w-full px-3 py-3
        rounded-lg border border-[var(--border-primary)]
        bg-transparent hover:bg-[var(--bg-hover)]
        text-[var(--text-primary)] text-sm font-medium
        transition-colors duration-150
        ${collapsed ? 'justify-center' : ''}
      `}
    >
      <PlusIcon className="w-4 h-4 flex-shrink-0" />
      {!collapsed && <span>新对话</span>}
    </button>
  );
}
