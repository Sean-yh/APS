'use client';

import {
  createContext,
  useContext,
  useReducer,
  useEffect,
  useCallback,
  useMemo,
  type ReactNode,
} from 'react';
import type {
  ChatState,
  ChatAction,
  ChatContextType,
  Message,
  ChatSession,
} from '@/types/chat';
import {
  saveState,
  loadState,
  initialState,
  createNewSession,
  generateId,
  generateSessionTitle,
} from '@/lib/storage';

// Reducer
function chatReducer(state: ChatState, action: ChatAction): ChatState {
  switch (action.type) {
    case 'CREATE_SESSION': {
      return {
        ...state,
        sessions: [action.payload, ...state.sessions],
        activeSessionId: action.payload.id,
      };
    }

    case 'DELETE_SESSION': {
      const newSessions = state.sessions.filter((s) => s.id !== action.payload);
      let newActiveId = state.activeSessionId;

      // 如果删除的是当前激活的会话，切换到第一个或设为 null
      if (state.activeSessionId === action.payload) {
        newActiveId = newSessions.length > 0 ? newSessions[0].id : null;
      }

      return {
        ...state,
        sessions: newSessions,
        activeSessionId: newActiveId,
      };
    }

    case 'SET_ACTIVE_SESSION': {
      return {
        ...state,
        activeSessionId: action.payload,
      };
    }

    case 'UPDATE_SESSION': {
      return {
        ...state,
        sessions: state.sessions.map((s) =>
          s.id === action.payload.id
            ? { ...s, ...action.payload.updates, updatedAt: Date.now() }
            : s
        ),
      };
    }

    case 'ADD_MESSAGE': {
      return {
        ...state,
        sessions: state.sessions.map((s) => {
          if (s.id !== action.payload.sessionId) return s;

          const newMessages = [...s.messages, action.payload.message];

          // 如果是第一条用户消息，自动生成标题
          let newTitle = s.title;
          if (
            s.title === '新对话' &&
            action.payload.message.role === 'user' &&
            s.messages.length === 0
          ) {
            newTitle = generateSessionTitle(action.payload.message.content);
          }

          return {
            ...s,
            messages: newMessages,
            title: newTitle,
            updatedAt: Date.now(),
          };
        }),
      };
    }

    case 'UPDATE_MESSAGE': {
      return {
        ...state,
        sessions: state.sessions.map((s) => {
          if (s.id !== action.payload.sessionId) return s;

          return {
            ...s,
            messages: s.messages.map((m) =>
              m.id === action.payload.messageId
                ? { ...m, ...action.payload.updates }
                : m
            ),
            updatedAt: Date.now(),
          };
        }),
      };
    }

    case 'TOGGLE_SIDEBAR': {
      return {
        ...state,
        sidebarOpen: !state.sidebarOpen,
      };
    }

    case 'SET_SIDEBAR_OPEN': {
      return {
        ...state,
        sidebarOpen: action.payload,
      };
    }

    case 'LOAD_STATE': {
      return action.payload;
    }

    case 'CLEAR_SESSION_MESSAGES': {
      return {
        ...state,
        sessions: state.sessions.map((s) =>
          s.id === action.payload
            ? { ...s, messages: [], title: '新对话', updatedAt: Date.now() }
            : s
        ),
      };
    }

    default:
      return state;
  }
}

// Context
const ChatContext = createContext<ChatContextType | null>(null);

// Provider Props
interface ChatProviderProps {
  children: ReactNode;
}

// Provider Component
export function ChatProvider({ children }: ChatProviderProps) {
  const [state, dispatch] = useReducer(chatReducer, initialState);

  // 初始化时从 localStorage 加载状态
  useEffect(() => {
    const savedState = loadState();
    if (savedState.sessions.length > 0 || savedState.activeSessionId) {
      dispatch({ type: 'LOAD_STATE', payload: savedState });
    }
  }, []);

  // 状态变化时保存到 localStorage
  useEffect(() => {
    saveState(state);
  }, [state]);

  // 创建新会话
  const createSession = useCallback((): string => {
    const session = createNewSession();
    dispatch({ type: 'CREATE_SESSION', payload: session });
    return session.id;
  }, []);

  // 删除会话
  const deleteSession = useCallback((id: string) => {
    dispatch({ type: 'DELETE_SESSION', payload: id });
  }, []);

  // 设置活动会话
  const setActiveSession = useCallback((id: string | null) => {
    dispatch({ type: 'SET_ACTIVE_SESSION', payload: id });
  }, []);

  // 添加消息
  const addMessage = useCallback(
    (message: Omit<Message, 'id' | 'timestamp'>) => {
      if (!state.activeSessionId) return;

      const fullMessage: Message = {
        ...message,
        id: generateId(),
        timestamp: Date.now(),
      };

      dispatch({
        type: 'ADD_MESSAGE',
        payload: { sessionId: state.activeSessionId, message: fullMessage },
      });

      return fullMessage.id;
    },
    [state.activeSessionId]
  );

  // 更新消息
  const updateMessage = useCallback(
    (messageId: string, updates: Partial<Message>) => {
      if (!state.activeSessionId) return;

      dispatch({
        type: 'UPDATE_MESSAGE',
        payload: {
          sessionId: state.activeSessionId,
          messageId,
          updates,
        },
      });
    },
    [state.activeSessionId]
  );

  // 清除当前会话消息
  const clearCurrentSession = useCallback(() => {
    if (!state.activeSessionId) return;
    dispatch({ type: 'CLEAR_SESSION_MESSAGES', payload: state.activeSessionId });
  }, [state.activeSessionId]);

  // 切换侧边栏
  const toggleSidebar = useCallback(() => {
    dispatch({ type: 'TOGGLE_SIDEBAR' });
  }, []);

  // 获取当前活动会话
  const activeSession = useMemo<ChatSession | null>(() => {
    if (!state.activeSessionId) return null;
    return state.sessions.find((s) => s.id === state.activeSessionId) || null;
  }, [state.sessions, state.activeSessionId]);

  const value = useMemo<ChatContextType>(
    () => ({
      state,
      dispatch,
      createSession,
      deleteSession,
      setActiveSession,
      addMessage,
      updateMessage,
      clearCurrentSession,
      toggleSidebar,
      activeSession,
    }),
    [
      state,
      createSession,
      deleteSession,
      setActiveSession,
      addMessage,
      updateMessage,
      clearCurrentSession,
      toggleSidebar,
      activeSession,
    ]
  );

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

// Hook
export function useChatStore(): ChatContextType {
  const context = useContext(ChatContext);
  if (!context) {
    throw new Error('useChatStore must be used within a ChatProvider');
  }
  return context;
}
