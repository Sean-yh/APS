'use client';

import {
  createContext,
  useContext,
  useReducer,
  useCallback,
  useMemo,
  useEffect,
  useRef,
  type ReactNode,
} from 'react';
import type {
  WorkspaceState,
  WorkspaceAction,
  WorkspaceContextType,
  ComparisonInfo,
  SelectedGanttTask,
} from '@/types/workspace';

// 初始状态
const initialState: WorkspaceState = {
  showWorkspace: false,
  currentSchedule: null,
  comparisons: [],
  loading: false,
  error: null,
  focusedScheduleId: null,
  isManuallyOpened: false,
  selectedTask: null,
};

// Reducer
function workspaceReducer(state: WorkspaceState, action: WorkspaceAction): WorkspaceState {
  switch (action.type) {
    case 'SET_SHOW_WORKSPACE':
      return { ...state, showWorkspace: action.payload };

    case 'SET_CURRENT_SCHEDULE':
      return { ...state, currentSchedule: action.payload };

    case 'SET_COMPARISONS':
      return { ...state, comparisons: action.payload };

    case 'ADD_COMPARISON':
      return { ...state, comparisons: [...state.comparisons, action.payload] };

    case 'REMOVE_COMPARISON':
      return {
        ...state,
        comparisons: state.comparisons.filter((c) => c.id !== action.payload),
      };

    case 'CLEAR_COMPARISONS':
      return { ...state, comparisons: [] };

    case 'SET_LOADING':
      return { ...state, loading: action.payload };

    case 'SET_ERROR':
      return { ...state, error: action.payload };

    case 'SYNC_STATE':
      return { ...state, ...action.payload };

    case 'OPEN_SCHEDULE':
      return {
        ...state,
        showWorkspace: true,
        isManuallyOpened: true,
        focusedScheduleId: action.payload,
      };

    case 'CLOSE_WORKSPACE':
      return {
        ...state,
        showWorkspace: false,
        isManuallyOpened: false,
        focusedScheduleId: null,
      };

    case 'CLEAR_FOCUS':
      return {
        ...state,
        focusedScheduleId: null,
      };

    case 'SELECT_TASK':
      return {
        ...state,
        selectedTask: action.payload,
      };

    case 'DESELECT_TASK':
      return {
        ...state,
        selectedTask: null,
      };

    default:
      return state;
  }
}

// Context
const WorkspaceContext = createContext<WorkspaceContextType | null>(null);

// Provider Props
interface WorkspaceProviderProps {
  children: ReactNode;
  apiUrl?: string;
}

// Provider Component
export function WorkspaceProvider({
  children,
  apiUrl = 'http://localhost:8000',
}: WorkspaceProviderProps) {
  const [state, dispatch] = useReducer(workspaceReducer, initialState);
  const pollingRef = useRef<NodeJS.Timeout | null>(null);
  const lastSyncRef = useRef<{
    comparisonsKey: string;
    currentKey: string;
    currentTimestamp: string | null;
    hasCurrentSchedule: boolean;
  } | null>(null);
  const lastErrorRef = useRef<string | null>(null);

  // 刷新工作区状态
  const refreshWorkspace = useCallback(async () => {
    try {
      // 获取对比方案状态
      const statusRes = await fetch(`${apiUrl}/api/schedule/comparison/status`);
      if (!statusRes.ok) {
        throw new Error('Failed to fetch comparison status');
      }
      const statusData = await statusRes.json();

      // 构建对比方案列表
      const scheduleMetas = (statusData.schedules || [])
        .map((s: { id: string; label: string; timestamp: string; constraint?: Record<string, unknown> }) => ({
          id: s.id,
          label: s.label,
          timestamp: s.timestamp,
          constraint: s.constraint,
        }))
        .sort((a: { id: string }, b: { id: string }) => a.id.localeCompare(b.id));

      const comparisonsKey = JSON.stringify(
        scheduleMetas.map((s: { id: string; label: string; timestamp: string; constraint?: Record<string, unknown> }) => ({
          id: s.id,
          label: s.label,
          timestamp: s.timestamp,
          constraint: s.constraint || null,
        }))
      );

      const comparisons: ComparisonInfo[] = scheduleMetas.map((s: { id: string; label: string; timestamp: string; constraint?: Record<string, unknown> }) => ({
        id: s.id,
        label: s.label,
        timestamp: s.timestamp,
        ganttUrl: `${apiUrl}/api/schedule/gantt/comparison/${s.id}`,
        constraint: s.constraint,
      }));

      // 检查是否有当前排产（通过检查 gantt 文件是否存在）
      let hasCurrentSchedule = false;
      let ganttVersion: string | null = null;
      let ganttTimestamp: string | null = null;
      try {
        // 使用 HEAD 检查 gantt 端点是否可用（避免下载整个 HTML）
        const ganttRes = await fetch(`${apiUrl}/api/schedule/gantt`, {
          method: 'HEAD',
        });
        hasCurrentSchedule = ganttRes.ok;
        if (hasCurrentSchedule) {
          const lastModified = ganttRes.headers.get('last-modified');
          const etag = ganttRes.headers.get('etag');
          ganttVersion = etag || lastModified;
          if (lastModified) {
            const parsed = new Date(lastModified);
            if (!Number.isNaN(parsed.getTime())) {
              ganttTimestamp = parsed.toISOString();
            }
          }
        }
      } catch {
        hasCurrentSchedule = false;
      }

      const currentKey = hasCurrentSchedule
        ? `current:${ganttVersion || ganttTimestamp || 'unknown'}`
        : 'none';

      const prev = lastSyncRef.current;
      let currentTimestamp: string | null = null;
      if (hasCurrentSchedule) {
        if (ganttTimestamp) {
          currentTimestamp = ganttTimestamp;
        } else if (prev && prev.currentKey === currentKey) {
          currentTimestamp = prev.currentTimestamp;
        } else {
          currentTimestamp = new Date().toISOString();
        }
      }

      const nextSnapshot = {
        comparisonsKey,
        currentKey,
        currentTimestamp,
        hasCurrentSchedule,
      };

      const hadError = lastErrorRef.current !== null;
      lastErrorRef.current = null;

      // Avoid re-render storms (which can disrupt text selection) by only dispatching when
      // something materially changed, or when we need to clear a previous error.
      if (
        !hadError &&
        prev &&
        prev.comparisonsKey === nextSnapshot.comparisonsKey &&
        prev.currentKey === nextSnapshot.currentKey &&
        prev.currentTimestamp === nextSnapshot.currentTimestamp &&
        prev.hasCurrentSchedule === nextSnapshot.hasCurrentSchedule
      ) {
        return;
      }

      lastSyncRef.current = nextSnapshot;

      // 更新状态
      // 注意：不自动设置 showWorkspace，由 openSchedule/closeWorkspace 控制
      dispatch({
        type: 'SYNC_STATE',
        payload: {
          currentSchedule: hasCurrentSchedule
            ? {
                id: 'current',
                timestamp: currentTimestamp || new Date().toISOString(),
                ganttUrl: ganttVersion
                  ? `${apiUrl}/api/schedule/gantt?v=${encodeURIComponent(ganttVersion)}`
                  : `${apiUrl}/api/schedule/gantt`,
              }
            : null,
          comparisons,
          error: null,
        },
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Unknown error';
      if (lastErrorRef.current === msg) return;
      lastErrorRef.current = msg;
      dispatch({
        type: 'SET_ERROR',
        payload: msg,
      });
    }
  }, [apiUrl]);

  // 删除对比方案
  const deleteComparison = useCallback(
    async (id: string) => {
      try {
        dispatch({ type: 'SET_LOADING', payload: true });
        const res = await fetch(`${apiUrl}/api/schedule/comparison/${id}`, {
          method: 'DELETE',
        });
        if (!res.ok) {
          throw new Error('Failed to delete comparison');
        }
        dispatch({ type: 'REMOVE_COMPARISON', payload: id });
      } catch (err) {
        dispatch({
          type: 'SET_ERROR',
          payload: err instanceof Error ? err.message : 'Unknown error',
        });
      } finally {
        dispatch({ type: 'SET_LOADING', payload: false });
      }
    },
    [apiUrl]
  );

  // 应用对比方案
  const applyComparison = useCallback(
    async (id: string) => {
      try {
        dispatch({ type: 'SET_LOADING', payload: true });
        const res = await fetch(`${apiUrl}/api/schedule/comparison/${id}/apply`, {
          method: 'POST',
        });
        if (!res.ok) {
          throw new Error('Failed to apply comparison');
        }
        // 清空对比列表并刷新
        dispatch({ type: 'CLEAR_COMPARISONS' });
        await refreshWorkspace();
      } catch (err) {
        dispatch({
          type: 'SET_ERROR',
          payload: err instanceof Error ? err.message : 'Unknown error',
        });
      } finally {
        dispatch({ type: 'SET_LOADING', payload: false });
      }
    },
    [apiUrl, refreshWorkspace]
  );

  // 清空所有对比方案
  const clearComparisons = useCallback(async () => {
    try {
      dispatch({ type: 'SET_LOADING', payload: true });
      const res = await fetch(`${apiUrl}/api/schedule/comparison`, {
        method: 'DELETE',
      });
      if (!res.ok) {
        throw new Error('Failed to clear comparisons');
      }
      dispatch({ type: 'CLEAR_COMPARISONS' });
    } catch (err) {
      dispatch({
        type: 'SET_ERROR',
        payload: err instanceof Error ? err.message : 'Unknown error',
      });
    } finally {
      dispatch({ type: 'SET_LOADING', payload: false });
    }
  }, [apiUrl]);

  // 打开工作区并聚焦到指定方案（Canvas 风格）
  const openSchedule = useCallback((scheduleId: string) => {
    dispatch({ type: 'OPEN_SCHEDULE', payload: scheduleId });
  }, []);

  // 关闭手动打开的工作区
  const closeWorkspace = useCallback(() => {
    dispatch({ type: 'CLOSE_WORKSPACE' });
  }, []);

  // 选中甘特图任务
  const selectTask = useCallback((task: SelectedGanttTask) => {
    dispatch({ type: 'SELECT_TASK', payload: task });
  }, []);

  // 取消选中任务
  const deselectTask = useCallback(() => {
    dispatch({ type: 'DESELECT_TASK' });
  }, []);

  // 初始加载和轮询
  useEffect(() => {
    // 初始加载
    refreshWorkspace();

    // 设置轮询（每 3 秒检查一次）
    pollingRef.current = setInterval(() => {
      refreshWorkspace();
    }, 3000);

    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
      }
    };
  }, [refreshWorkspace]);

  const value = useMemo<WorkspaceContextType>(
    () => ({
      state,
      dispatch,
      refreshWorkspace,
      deleteComparison,
      applyComparison,
      clearComparisons,
      openSchedule,
      closeWorkspace,
      selectTask,
      deselectTask,
    }),
    [state, refreshWorkspace, deleteComparison, applyComparison, clearComparisons, openSchedule, closeWorkspace, selectTask, deselectTask]
  );

  return (
    <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>
  );
}

// Hook
export function useWorkspaceStore(): WorkspaceContextType {
  const context = useContext(WorkspaceContext);
  if (!context) {
    throw new Error('useWorkspaceStore must be used within a WorkspaceProvider');
  }
  return context;
}
