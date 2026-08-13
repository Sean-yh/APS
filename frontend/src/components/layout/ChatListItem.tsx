'use client';

import { useState } from 'react';
import { ChatIcon, TrashIcon } from '@/components/ui/Icons';
import type { ChatSession } from '@/types/chat';

interface ChatListItemProps {
  session: ChatSession;
  isActive: boolean;
  onClick: () => void;
  onDelete: () => void;
  collapsed?: boolean;
}

export function ChatListItem({
  session,
  isActive,
  onClick,
  onDelete,
  collapsed = false,
}: ChatListItemProps) {
  const [showDelete, setShowDelete] = useState(false);

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    onDelete();
  };

  if (collapsed) {
    return (
      <button
        onClick={onClick}
        className={`
          w-full p-2 rounded-lg flex items-center justify-center
          transition-colors duration-150
          ${isActive
            ? 'bg-[var(--bg-active)] text-[var(--text-primary)]'
            : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]'
          }
        `}
        title={session.title}
      >
        <ChatIcon className="w-5 h-5" />
      </button>
    );
  }

  return (
    <div
      onClick={onClick}
      onMouseEnter={() => setShowDelete(true)}
      onMouseLeave={() => setShowDelete(false)}
      className={`
        w-full px-3 py-2.5 rounded-lg flex items-center gap-3 group
        transition-colors duration-150 text-left cursor-pointer
        ${isActive
          ? 'bg-[var(--bg-active)] text-[var(--text-primary)]'
          : 'text-[var(--text-secondary)] hover:bg-[var(--bg-hover)] hover:text-[var(--text-primary)]'
        }
      `}
    >
      <ChatIcon className="w-4 h-4 flex-shrink-0" />
      <span className="flex-1 text-sm truncate">{session.title}</span>

      {showDelete && (
        <button
          onClick={handleDelete}
          className="p-1 rounded hover:bg-[var(--bg-tertiary)] text-[var(--text-tertiary)] hover:text-red-400 transition-colors"
          title="删除对话"
        >
          <TrashIcon className="w-4 h-4" />
        </button>
      )}
    </div>
  );
}
