// 表单卡片类型
export interface FormCard {
  id: string;
  type: 'maintenance' | 'holiday';
  status: 'pending' | 'submitted' | 'cancelled';
  data?: {
    machine_id?: string;
    reason?: string;
    start?: string;
    end?: string;
    name?: string;  // for holiday
  };
}

// 排产预览卡片类型
export interface ScheduleCard {
  id: string;
  scheduleId: string;           // 'current' 或对比方案 ID
  type: 'current' | 'comparison';
  label: string;                // "当前排产" 或 "重排方案 A"
  timestamp: string;
  ganttUrl: string;
  constraint?: {
    order_ids?: number[];
    porefs?: string[];
    new_deadline?: string;
    priority_lock?: boolean;
  };
}

// ERP 导出卡片类型
export interface ErpExportCard {
  id: string;
  status: 'pending' | 'success' | 'error';
  orderCount: number;
  totalQuantity: number;
  dateRange: string;
  timestamp: string;
}

// 消息类型
export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  toolCalls?: Array<{
    name: string;
    input: Record<string, unknown>;
    output?: string;
  }>;
  formCards?: FormCard[];        // 表单卡片
  scheduleCards?: ScheduleCard[]; // 排产预览卡片
  erpExportCards?: ErpExportCard[]; // ERP 导出卡片
  isStreaming?: boolean;
  timestamp: number;
}

// 聊天会话类型
export interface ChatSession {
  id: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
}

// 聊天状态类型
export interface ChatState {
  sessions: ChatSession[];
  activeSessionId: string | null;
  sidebarOpen: boolean;
}

// 聊天 Action 类型
export type ChatAction =
  | { type: 'CREATE_SESSION'; payload: ChatSession }
  | { type: 'DELETE_SESSION'; payload: string }
  | { type: 'SET_ACTIVE_SESSION'; payload: string | null }
  | { type: 'UPDATE_SESSION'; payload: { id: string; updates: Partial<ChatSession> } }
  | { type: 'ADD_MESSAGE'; payload: { sessionId: string; message: Message } }
  | { type: 'UPDATE_MESSAGE'; payload: { sessionId: string; messageId: string; updates: Partial<Message> } }
  | { type: 'TOGGLE_SIDEBAR' }
  | { type: 'SET_SIDEBAR_OPEN'; payload: boolean }
  | { type: 'LOAD_STATE'; payload: ChatState }
  | { type: 'CLEAR_SESSION_MESSAGES'; payload: string };

// 聊天上下文类型
export interface ChatContextType {
  state: ChatState;
  dispatch: React.Dispatch<ChatAction>;
  // 便捷方法
  createSession: () => string;
  deleteSession: (id: string) => void;
  setActiveSession: (id: string | null) => void;
  addMessage: (message: Omit<Message, 'id' | 'timestamp'>) => string | undefined;
  updateMessage: (messageId: string, updates: Partial<Message>) => void;
  clearCurrentSession: () => void;
  toggleSidebar: () => void;
  activeSession: ChatSession | null;
}
