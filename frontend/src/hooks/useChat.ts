'use client';

import { useState, useCallback, useRef } from 'react';
import { useChatStore } from '@/stores/chatStore';
import type { Message, FormCard, ScheduleCard, ErpExportCard } from '@/types/chat';

// 保留原始 Message 类型导出以保持兼容性
export type { Message, FormCard, ScheduleCard, ErpExportCard } from '@/types/chat';

interface UseChatOptions {
  apiUrl?: string;
}

export function useChat(options: UseChatOptions = {}) {
  const { apiUrl = 'http://localhost:8000' } = options;

  const {
    state,
    activeSession,
    createSession,
    addMessage,
    updateMessage,
    clearCurrentSession,
  } = useChatStore();

  const [isLoading, setIsLoading] = useState(false);
  const abortRef = useRef<AbortController | null>(null);

  // 获取当前会话的消息
  const messages = activeSession?.messages || [];

  // 确保有活动会话
  const ensureSession = useCallback(() => {
    if (!state.activeSessionId) {
      return createSession();
    }
    return state.activeSessionId;
  }, [state.activeSessionId, createSession]);

  const sendMessage = useCallback(
    async (content: string) => {
      if (!content.trim()) return;

      // 确保有活动会话
      ensureSession();

      // 添加用户消息
      addMessage({
        role: 'user',
        content: content.trim(),
      });

      setIsLoading(true);

      // 添加占位助手消息并保存其 ID
      const newAssistantId = addMessage({
        role: 'assistant',
        content: '',
        toolCalls: [],
        isStreaming: true,
      });

      // 在 try 外部定义，以便 catch 中可以访问
      let accumulatedContent = '';

      try {
        abortRef.current = new AbortController();
        const nowMs = () => Date.now();

        // If the user is selecting/copying text in the chat history, continuous streaming
        // updates can replace DOM nodes and collapse the selection. We pause UI updates
        // while there is an active selection, then flush once selection is cleared.
        const canUpdateUi = () => {
          const sel = typeof window !== 'undefined' ? window.getSelection?.() : null;
          return !(sel && !sel.isCollapsed);
        };

        let lastUiUpdateAt = 0;
        let pendingPatch: Partial<Message> | null = null;
        let flushTimer: ReturnType<typeof setTimeout> | null = null;

        const queuePatch = (patch: Partial<Message>) => {
          pendingPatch = { ...(pendingPatch || {}), ...patch };
        };

        const tryFlush = (force = false) => {
          if (!newAssistantId || !pendingPatch) return;
          // Never disrupt an active selection; "force" only bypasses throttling.
          if (!canUpdateUi()) return;
          if (!force) {
            const dt = nowMs() - lastUiUpdateAt;
            if (dt < 80) return; // throttle to avoid re-render storms
          }
          updateMessage(newAssistantId, pendingPatch);
          pendingPatch = null;
          lastUiUpdateAt = nowMs();
        };

        const scheduleFlush = () => {
          if (flushTimer) return;
          flushTimer = setTimeout(() => {
            flushTimer = null;
            tryFlush(false);
            // Keep retrying while selection is active.
            if (pendingPatch && !canUpdateUi()) scheduleFlush();
          }, 120);
        };

        const response = await fetch(`${apiUrl}/api/chat/stream`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            message: content.trim(),
            session_id: state.activeSessionId || undefined,
          }),
          signal: abortRef.current.signal,
        });

        if (!response.ok) {
          throw new Error(`HTTP error! status: ${response.status}`);
        }

        const reader = response.body?.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        let toolCalls: Message['toolCalls'] = [];
        let formCards: FormCard[] = [];
        let scheduleCards: ScheduleCard[] = [];
        let erpExportCards: ErpExportCard[] = [];

        while (reader) {
          const { done, value } = await reader.read();
          if (done) break;

          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\n');
          buffer = lines.pop() || '';

          for (const line of lines) {
            if (line.startsWith('data: ')) {
              try {
                const data = JSON.parse(line.slice(6));

                if (data.type === 'content') {
                  accumulatedContent += data.content;
                  queuePatch({ content: accumulatedContent });
                  tryFlush(false);
                  if (pendingPatch) scheduleFlush();
                }

                if (data.type === 'tool_call') {
                  toolCalls = [
                    ...toolCalls,
                    { name: data.tool_name, input: data.tool_input },
                  ];
                  queuePatch({ toolCalls });
                  tryFlush(false);
                  if (pendingPatch) scheduleFlush();
                }

                if (data.type === 'tool_result') {
                  if (toolCalls.length > 0) {
                    toolCalls[toolCalls.length - 1].output = data.tool_output;
                    queuePatch({ toolCalls: [...toolCalls] });
                    tryFlush(false);
                    if (pendingPatch) scheduleFlush();
                  }
                }

                if (data.type === 'form_card') {
                  const formCard: FormCard = {
                    id: `form_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
                    type: data.form_type,
                    status: 'pending',
                    data: data.form_data || {},
                  };
                  formCards = [...formCards, formCard];
                  queuePatch({ formCards });
                  tryFlush(false);
                  if (pendingPatch) scheduleFlush();
                }

                if (data.type === 'schedule_card') {
                  const scheduleCard: ScheduleCard = {
                    id: `schedule_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
                    scheduleId: data.schedule_id,
                    type: data.schedule_type,
                    label: data.label,
                    timestamp: data.timestamp,
                    ganttUrl: data.gantt_url,
                    constraint: data.constraint,
                  };
                  scheduleCards = [...scheduleCards, scheduleCard];
                  queuePatch({ scheduleCards });
                  tryFlush(false);
                  if (pendingPatch) scheduleFlush();
                }

                if (data.type === 'erp_export_card') {
                  const erpExportCard: ErpExportCard = {
                    id: `erp_export_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`,
                    status: data.status,
                    orderCount: data.orderCount,
                    totalQuantity: data.totalQuantity,
                    dateRange: data.dateRange,
                    timestamp: data.timestamp,
                  };
                  erpExportCards = [...erpExportCards, erpExportCard];
                  queuePatch({ erpExportCards });
                  tryFlush(false);
                  if (pendingPatch) scheduleFlush();
                }

                if (data.type === 'done') {
                  queuePatch({ isStreaming: false, content: accumulatedContent, toolCalls, formCards, scheduleCards, erpExportCards });
                  tryFlush(true);
                  if (pendingPatch) scheduleFlush();
                }

                if (data.type === 'error') {
                  if (newAssistantId) {
                    pendingPatch = null;
                    updateMessage(newAssistantId, {
                      content: data.content || '发生错误',
                      isStreaming: false,
                    });
                  }
                }
              } catch (e) {
                console.error('Failed to parse SSE data:', e);
              }
            }
          }
        }

        // 确保流结束时标记为完成
        if (newAssistantId) {
          // Ensure the last buffered patch is applied even if the client paused updates while selecting text.
          queuePatch({ isStreaming: false, content: accumulatedContent, toolCalls, formCards, scheduleCards, erpExportCards });
          tryFlush(true);
          if (pendingPatch) scheduleFlush();
        }
      } catch (error) {
        if ((error as Error).name === 'AbortError') {
          // 用户取消
          if (newAssistantId) {
            updateMessage(newAssistantId, {
              content: accumulatedContent + '\n\n[已取消]',
              isStreaming: false,
            });
          }
        } else {
          console.error('Chat error:', error);
          if (newAssistantId) {
            updateMessage(newAssistantId, {
              content: `错误: ${(error as Error).message}`,
              isStreaming: false,
            });
          }
        }
      } finally {
        setIsLoading(false);
      }
    },
    [apiUrl, state.activeSessionId, ensureSession, addMessage, updateMessage]
  );

  const stopGeneration = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const clearMessages = useCallback(() => {
    clearCurrentSession();
  }, [clearCurrentSession]);

  return {
    messages,
    isLoading,
    sendMessage,
    stopGeneration,
    clearMessages,
    sessionId: state.activeSessionId,
    ensureSession,
  };
}
