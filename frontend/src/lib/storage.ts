import type { ChatState, ChatSession } from '@/types/chat';

const STORAGE_KEY = 'l2-chat-state';

// 默认初始状态
export const initialState: ChatState = {
  sessions: [],
  activeSessionId: null,
  sidebarOpen: true,
};

// 保存状态到 localStorage
export function saveState(state: ChatState): void {
  if (typeof window === 'undefined') return;

  try {
    const serialized = JSON.stringify(state);
    localStorage.setItem(STORAGE_KEY, serialized);
  } catch (error) {
    console.error('Failed to save state to localStorage:', error);
  }
}

// 从 localStorage 加载状态
export function loadState(): ChatState {
  if (typeof window === 'undefined') return initialState;

  try {
    const serialized = localStorage.getItem(STORAGE_KEY);
    if (!serialized) return initialState;

    const parsed = JSON.parse(serialized) as ChatState;

    // 验证数据结构
    if (!Array.isArray(parsed.sessions)) {
      return initialState;
    }

    return {
      sessions: parsed.sessions,
      activeSessionId: parsed.activeSessionId,
      sidebarOpen: parsed.sidebarOpen ?? true,
    };
  } catch (error) {
    console.error('Failed to load state from localStorage:', error);
    return initialState;
  }
}

// 清除状态
export function clearState(): void {
  if (typeof window === 'undefined') return;

  try {
    localStorage.removeItem(STORAGE_KEY);
  } catch (error) {
    console.error('Failed to clear state from localStorage:', error);
  }
}

// 生成唯一 ID
export function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).substring(2, 9)}`;
}

// 创建新会话
export function createNewSession(): ChatSession {
  const now = Date.now();
  return {
    id: generateId(),
    title: '新对话',
    messages: [],
    createdAt: now,
    updatedAt: now,
  };
}

// 从第一条用户消息生成标题
export function generateSessionTitle(content: string): string {
  const trimmed = content.trim();
  if (trimmed.length <= 20) return trimmed;
  return trimmed.substring(0, 20) + '...';
}
