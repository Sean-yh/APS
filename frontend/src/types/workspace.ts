/**
 * 工作区状态类型定义
 */

/**
 * 选中的甘特图任务信息
 */
export interface SelectedGanttTask {
  /** 所属机器 */
  machine: string;
  /** 任务类型: forming, label, setup, idle */
  type: 'forming' | 'label' | 'setup' | 'idle' | string;
  /** SKU 编号 */
  sku: string | null;
  /** 订单 ID */
  orderId: number | null;
  /** 生产数量 */
  quantity: number | null;
  /** 开始时间 ISO 字符串 */
  start: string;
  /** 结束时间 ISO 字符串 */
  end: string;
  /** 持续时长（小时） */
  durationH: number | null;
  /** 来源方案 ID: 'current' 或对比方案 ID */
  sourceScheduleId: string;
}

/**
 * 从 iframe postMessage 接收的消息格式
 */
export interface GanttTaskSelectMessage {
  type: 'gantt:task:select' | 'gantt:task:deselect';
  payload: Omit<SelectedGanttTask, 'sourceScheduleId'> | null;
}

export interface ScheduleInfo {
  id: string;
  timestamp: string;
  ganttUrl: string;
}

export interface ComparisonInfo {
  id: string;
  label: string;
  timestamp: string;
  ganttUrl: string;
  constraint?: {
    order_ids?: number[];
    porefs?: string[];
    new_deadline?: string;
    priority_lock?: boolean;
  };
}

export interface WorkspaceState {
  /** 是否显示工作区（有排产数据时显示） */
  showWorkspace: boolean;
  /** 当前排产信息 */
  currentSchedule: ScheduleInfo | null;
  /** 重排方案列表 */
  comparisons: ComparisonInfo[];
  /** 加载状态 */
  loading: boolean;
  /** 错误信息 */
  error: string | null;
  /** 当前聚焦的方案 ID（用于 Canvas 风格高亮） */
  focusedScheduleId: string | null;
  /** 是否通过卡片手动打开工作区 */
  isManuallyOpened: boolean;
  /** 选中的甘特图任务 */
  selectedTask: SelectedGanttTask | null;
}

export type WorkspaceAction =
  | { type: 'SET_SHOW_WORKSPACE'; payload: boolean }
  | { type: 'SET_CURRENT_SCHEDULE'; payload: ScheduleInfo | null }
  | { type: 'SET_COMPARISONS'; payload: ComparisonInfo[] }
  | { type: 'ADD_COMPARISON'; payload: ComparisonInfo }
  | { type: 'REMOVE_COMPARISON'; payload: string }
  | { type: 'CLEAR_COMPARISONS' }
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_ERROR'; payload: string | null }
  | { type: 'SYNC_STATE'; payload: Partial<WorkspaceState> }
  | { type: 'OPEN_SCHEDULE'; payload: string }
  | { type: 'CLOSE_WORKSPACE' }
  | { type: 'CLEAR_FOCUS' }
  | { type: 'SELECT_TASK'; payload: SelectedGanttTask }
  | { type: 'DESELECT_TASK' };

export interface WorkspaceContextType {
  state: WorkspaceState;
  dispatch: React.Dispatch<WorkspaceAction>;
  /** 刷新工作区状态（从后端获取最新数据） */
  refreshWorkspace: () => Promise<void>;
  /** 删除指定的对比方案 */
  deleteComparison: (id: string) => Promise<void>;
  /** 应用指定的对比方案为当前排产 */
  applyComparison: (id: string) => Promise<void>;
  /** 清空所有对比方案 */
  clearComparisons: () => Promise<void>;
  /** 打开工作区并聚焦到指定方案（Canvas 风格） */
  openSchedule: (scheduleId: string) => void;
  /** 关闭手动打开的工作区 */
  closeWorkspace: () => void;
  /** 选中甘特图任务 */
  selectTask: (task: SelectedGanttTask) => void;
  /** 取消选中任务 */
  deselectTask: () => void;
}
