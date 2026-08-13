'use client';

import { useChatStore } from '@/stores/chatStore';
import { NewChatButton } from './NewChatButton';
import { ChatList } from './ChatList';

export function LeftPanel() {
  const {
    state,
    createSession,
    deleteSession,
    setActiveSession,
  } = useChatStore();

  const { sessions, activeSessionId } = state;

  return (
    <div className="flex flex-col h-full">
      {/* New Chat Button */}
      <div className="p-3">
        <NewChatButton onClick={() => createSession()} />
      </div>

      {/* Chat List */}
      <div className="flex-1 overflow-y-auto">
        <ChatList
          sessions={sessions}
          activeSessionId={activeSessionId}
          onSelectSession={setActiveSession}
          onDeleteSession={deleteSession}
        />
      </div>
    </div>
  );
}
