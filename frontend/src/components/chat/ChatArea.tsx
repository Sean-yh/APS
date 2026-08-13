'use client';

import { useCallback } from 'react';
import { useChat } from '@/hooks/useChat';
import { useChatStore } from '@/stores/chatStore';
import { useWorkspaceStore } from '@/stores/workspaceStore';
import { MessageList } from './MessageList';
import { InputArea } from './InputArea';
import type { FormCard } from '@/types/chat';
import type { SelectedGanttTask } from '@/types/workspace';

interface ChatAreaProps {
  apiUrl?: string;
}

export function ChatArea({ apiUrl = 'http://localhost:8000' }: ChatAreaProps) {
  const { messages, isLoading, sendMessage, stopGeneration, ensureSession } =
    useChat({ apiUrl });
  const { updateMessage } = useChatStore();
  const { deselectTask } = useWorkspaceStore();

  // 构建带任务上下文的消息
  const buildMessageWithContext = useCallback((content: string, task?: SelectedGanttTask): string => {
    if (!task) return content;

    // 格式化时间
    const formatTime = (isoStr: string) => {
      try {
        return new Date(isoStr).toLocaleString('zh-CN', {
          month: '2-digit',
          day: '2-digit',
          hour: '2-digit',
          minute: '2-digit',
        });
      } catch {
        return isoStr;
      }
    };

    // 获取任务类型中文名
    const getTypeName = (type: string) => {
      const typeMap: Record<string, string> = {
        forming: '成型',
        label: '贴标',
        setup: '换色',
        idle: '空闲',
      };
      return typeMap[type] || type;
    };

    // 构建上下文前缀
    const parts = [
      `机器 ${task.machine}`,
      task.sku ? `SKU ${task.sku}` : `类型 ${getTypeName(task.type)}`,
    ];
    if (task.orderId) parts.push(`订单 #${task.orderId}`);
    parts.push(`时间 ${formatTime(task.start)} - ${formatTime(task.end)}`);
    if (task.quantity) parts.push(`数量 ${task.quantity.toLocaleString()} 件`);

    return `[任务上下文: ${parts.join(' / ')}]\n\n${content}`;
  }, []);

  const handleSuggestionClick = (suggestion: string) => {
    ensureSession();
    sendMessage(suggestion);
  };

  // Handle form card updates
  const handleFormCardUpdate = useCallback((messageId: string, cardId: string, updates: Partial<FormCard>) => {
    // Find the message and update the specific form card
    const message = messages.find(m => m.id === messageId);
    if (!message || !message.formCards) return;

    const updatedFormCards = message.formCards.map(card =>
      card.id === cardId ? { ...card, ...updates } : card
    );

    updateMessage(messageId, { formCards: updatedFormCards });
  }, [messages, updateMessage]);

  return (
    <div className="flex-1 flex flex-col h-full bg-[var(--bg-primary)] overflow-hidden">
      {/* Messages */}
      <MessageList
        messages={messages}
        onSuggestionClick={handleSuggestionClick}
        onFormCardUpdate={handleFormCardUpdate}
      />

      {/* Input */}
      <InputArea
        onSend={(content, context) => {
          ensureSession();
          const messageWithContext = buildMessageWithContext(content, context);
          sendMessage(messageWithContext);
          // 发送后清除选中状态
          if (context) {
            deselectTask();
          }
        }}
        onStop={stopGeneration}
        isLoading={isLoading}
      />
    </div>
  );
}
