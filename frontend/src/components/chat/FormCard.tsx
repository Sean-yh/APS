'use client';

import { useEffect, useState } from 'react';
import type { FormCard as FormCardType } from '@/types/chat';

// Must match backend VALID_MACHINE_IDS (ai/calendar_store.py)
const VALID_MACHINE_IDS = [
  'ROTARY-1',
  'LABEL-1',
  'LABEL-2',
  'ROTARY-2',
  'LABEL-3',
  'LABEL-5',
  'ROTARY-3',
  'LABEL-4',
  'LABEL-6',
];

interface FormCardProps {
  card: FormCardType;
  onSubmit: (cardId: string, data: FormCardType['data']) => Promise<void>;
  onCancel: (cardId: string) => void;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

type DowntimeHoliday = {
  name: string;
  start: string;
  end: string;
};

type DowntimeMaintenance = {
  machine_id: string;
  start: string;
  end: string;
  reason?: string;
};

type DowntimeState = {
  holidays: DowntimeHoliday[];
  maintenance: DowntimeMaintenance[];
};

function isRecord(v: unknown): v is Record<string, unknown> {
  return typeof v === 'object' && v !== null;
}

function getString(v: unknown): string | null {
  return typeof v === 'string' ? v : null;
}

function toHoliday(v: unknown): DowntimeHoliday | null {
  if (!isRecord(v)) return null;
  const start = getString(v.start);
  const end = getString(v.end);
  if (!start || !end) return null;
  return { name: getString(v.name) ?? '假期', start, end };
}

function toMaintenance(v: unknown): DowntimeMaintenance | null {
  if (!isRecord(v)) return null;
  const machine_id = getString(v.machine_id);
  const start = getString(v.start);
  const end = getString(v.end);
  if (!machine_id || !start || !end) return null;
  return { machine_id, start, end, reason: getString(v.reason) ?? undefined };
}

function getDetailMessage(payload: unknown): string | null {
  if (!isRecord(payload)) return null;
  const detail = payload.detail;
  return typeof detail === 'string' ? detail : null;
}

interface MaintenanceFormData {
  machine_id: string;
  reason: string;
  start: string;
  end: string;
}

interface HolidayFormData {
  name: string;
  start: string;
  end: string;
}

function MaintenanceForm({
  onSubmit,
  onCancel,
  isSubmitting,
}: {
  onSubmit: (data: MaintenanceFormData) => void | Promise<void>;
  onCancel: () => void;
  isSubmitting: boolean;
}) {
  const [formData, setFormData] = useState<MaintenanceFormData>({
    machine_id: VALID_MACHINE_IDS[0],
    reason: '',
    start: '',
    end: '',
  });
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Validation
    if (!formData.machine_id) {
      setError('请选择机器');
      return;
    }
    if (!formData.reason.trim()) {
      setError('请输入停机原因');
      return;
    }
    if (!formData.start) {
      setError('请选择开始时间');
      return;
    }
    if (!formData.end) {
      setError('请选择结束时间');
      return;
    }
    if (new Date(formData.start) >= new Date(formData.end)) {
      setError('结束时间必须晚于开始时间');
      return;
    }

    onSubmit(formData);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1">
          机器
        </label>
        <select
          value={formData.machine_id}
          onChange={(e) => setFormData({ ...formData, machine_id: e.target.value })}
          className="w-full px-3 py-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]"
          disabled={isSubmitting}
        >
          {VALID_MACHINE_IDS.map((id) => (
            <option key={id} value={id}>
              {id}
            </option>
          ))}
        </select>
      </div>

      <div>
        <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1">
          停机原因
        </label>
        <input
          type="text"
          value={formData.reason}
          onChange={(e) => setFormData({ ...formData, reason: e.target.value })}
          placeholder="例如：年度保养、换模、维修..."
          className="w-full px-3 py-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] text-[var(--text-primary)] placeholder-[var(--text-tertiary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]"
          disabled={isSubmitting}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1">
            开始时间
          </label>
          <input
            type="datetime-local"
            value={formData.start}
            onChange={(e) => setFormData({ ...formData, start: e.target.value })}
            className="w-full px-3 py-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]"
            disabled={isSubmitting}
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1">
            结束时间
          </label>
          <input
            type="datetime-local"
            value={formData.end}
            onChange={(e) => setFormData({ ...formData, end: e.target.value })}
            className="w-full px-3 py-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]"
            disabled={isSubmitting}
          />
        </div>
      </div>

      {error && (
        <p className="text-sm text-red-500">{error}</p>
      )}

      <div className="flex gap-3 pt-2">
        <button
          type="submit"
          disabled={isSubmitting}
          className="flex-1 px-4 py-2 bg-[var(--accent-primary)] text-white rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity font-medium"
        >
          {isSubmitting ? '提交中...' : '确认添加'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={isSubmitting}
          className="px-4 py-2 border border-[var(--border-primary)] text-[var(--text-secondary)] rounded-lg hover:bg-[var(--bg-tertiary)] disabled:opacity-50 transition-colors"
        >
          取消
        </button>
      </div>
    </form>
  );
}

function HolidayForm({
  onSubmit,
  onCancel,
  isSubmitting,
}: {
  onSubmit: (data: HolidayFormData) => void | Promise<void>;
  onCancel: () => void;
  isSubmitting: boolean;
}) {
  const [formData, setFormData] = useState<HolidayFormData>({
    name: '',
    start: '',
    end: '',
  });
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);

    // Validation
    if (!formData.name.trim()) {
      setError('请输入假期名称');
      return;
    }
    if (!formData.start) {
      setError('请选择开始日期');
      return;
    }
    if (!formData.end) {
      setError('请选择结束日期');
      return;
    }
    if (new Date(formData.start) > new Date(formData.end)) {
      setError('结束日期不能早于开始日期');
      return;
    }

    onSubmit(formData);
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1">
          假期名称
        </label>
        <input
          type="text"
          value={formData.name}
          onChange={(e) => setFormData({ ...formData, name: e.target.value })}
          placeholder="例如：春节、国庆节、中秋节..."
          className="w-full px-3 py-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] text-[var(--text-primary)] placeholder-[var(--text-tertiary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]"
          disabled={isSubmitting}
        />
      </div>

      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1">
            开始日期
          </label>
          <input
            type="date"
            value={formData.start}
            onChange={(e) => setFormData({ ...formData, start: e.target.value })}
            className="w-full px-3 py-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]"
            disabled={isSubmitting}
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-[var(--text-secondary)] mb-1">
            结束日期
          </label>
          <input
            type="date"
            value={formData.end}
            onChange={(e) => setFormData({ ...formData, end: e.target.value })}
            className="w-full px-3 py-2 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] text-[var(--text-primary)] focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)]"
            disabled={isSubmitting}
          />
        </div>
      </div>

      {error && (
        <p className="text-sm text-red-500">{error}</p>
      )}

      <div className="flex gap-3 pt-2">
        <button
          type="submit"
          disabled={isSubmitting}
          className="flex-1 px-4 py-2 bg-[var(--accent-primary)] text-white rounded-lg hover:opacity-90 disabled:opacity-50 transition-opacity font-medium"
        >
          {isSubmitting ? '提交中...' : '确认添加'}
        </button>
        <button
          type="button"
          onClick={onCancel}
          disabled={isSubmitting}
          className="px-4 py-2 border border-[var(--border-primary)] text-[var(--text-secondary)] rounded-lg hover:bg-[var(--bg-tertiary)] disabled:opacity-50 transition-colors"
        >
          取消
        </button>
      </div>
    </form>
  );
}

export function FormCard({ card, onSubmit, onCancel }: FormCardProps) {
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [submitResult, setSubmitResult] = useState<{ success: boolean; message: string } | null>(null);
  const [downtime, setDowntime] = useState<DowntimeState | null>(null);
  const [manageMsg, setManageMsg] = useState<string | null>(null);

  const refreshDowntime = async () => {
    try {
      const res = await fetch(`${API_URL}/api/calendar/downtime`, { cache: 'no-store' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: unknown = await res.json();
      const record = isRecord(data) ? data : {};
      setDowntime({
        holidays: Array.isArray(record.holidays)
          ? record.holidays.map(toHoliday).filter((x): x is DowntimeHoliday => x !== null)
          : [],
        maintenance: Array.isArray(record.maintenance)
          ? record.maintenance.map(toMaintenance).filter((x): x is DowntimeMaintenance => x !== null)
          : [],
      });
    } catch {
      // Best-effort; don't block the form on this.
      setDowntime({ holidays: [], maintenance: [] });
    }
  };

  useEffect(() => {
    refreshDowntime();
  }, []);

  const handleMaintenanceSubmit = async (data: MaintenanceFormData) => {
    setIsSubmitting(true);
    setSubmitResult(null);

    try {
      // Format datetime to ISO format (YYYY-MM-DDTHH:MM)
      const formattedData = {
        ...data,
        start: data.start.replace(' ', 'T'),
        end: data.end.replace(' ', 'T'),
      };

      const response = await fetch(`${API_URL}/api/calendar/maintenance`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(formattedData),
      });

      const result: unknown = await response.json().catch(() => null);

      if (response.ok) {
        setSubmitResult({ success: true, message: `已添加 ${data.machine_id} 的维护计划` });
        await onSubmit(card.id, formattedData);
        await refreshDowntime();
      } else {
        setSubmitResult({ success: false, message: getDetailMessage(result) || '添加失败' });
      }
    } catch {
      setSubmitResult({ success: false, message: '网络错误，请重试' });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleHolidaySubmit = async (data: HolidayFormData) => {
    setIsSubmitting(true);
    setSubmitResult(null);

    try {
      const response = await fetch(`${API_URL}/api/calendar/holiday`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(data),
      });

      const result: unknown = await response.json().catch(() => null);

      if (response.ok) {
        setSubmitResult({ success: true, message: `已添加假期：${data.name}` });
        await onSubmit(card.id, data);
        await refreshDowntime();
      } else {
        setSubmitResult({ success: false, message: getDetailMessage(result) || '添加失败' });
      }
    } catch {
      setSubmitResult({ success: false, message: '网络错误，请重试' });
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleCancel = () => {
    onCancel(card.id);
  };

  const handleDeleteHoliday = async (index: number) => {
    setManageMsg(null);
    try {
      const res = await fetch(`${API_URL}/api/calendar/holiday/${index}`, { method: 'DELETE' });
      const payload: unknown = await res.json().catch(() => null);
      if (!res.ok) throw new Error(getDetailMessage(payload) || `HTTP ${res.status}`);
      setManageMsg('已删除假期');
      await refreshDowntime();
    } catch (e) {
      setManageMsg(`删除失败：${e instanceof Error ? e.message : 'Unknown error'}`);
    }
  };

  const handleDeleteMaintenance = async (index: number) => {
    setManageMsg(null);
    try {
      const res = await fetch(`${API_URL}/api/calendar/maintenance/${index}`, { method: 'DELETE' });
      const payload: unknown = await res.json().catch(() => null);
      if (!res.ok) throw new Error(getDetailMessage(payload) || `HTTP ${res.status}`);
      setManageMsg('已删除维护计划');
      await refreshDowntime();
    } catch (e) {
      setManageMsg(`删除失败：${e instanceof Error ? e.message : 'Unknown error'}`);
    }
  };

  const handleDeleteAll = async () => {
    if (!downtime) return;
    const list = card.type === 'holiday' ? downtime.holidays : downtime.maintenance;
    if (list.length === 0) return;
    const ok = window.confirm(
      `确定要删除全部 ${card.type === 'holiday' ? '假期' : '维护'} 吗？（共 ${list.length} 条）`,
    );
    if (!ok) return;

    setManageMsg(null);
    // Delete from the end to avoid index shifting.
    for (let i = list.length - 1; i >= 0; i -= 1) {
      await (card.type === 'holiday' ? handleDeleteHoliday(i) : handleDeleteMaintenance(i));
    }
    setManageMsg('已删除全部条目');
  };

  // If already submitted or cancelled, show status
  if (card.status === 'submitted' || submitResult?.success) {
    return (
      <div className="mt-3 p-4 rounded-xl border border-green-200 bg-green-50 dark:border-green-800 dark:bg-green-900/20">
        <div className="flex items-center gap-2 text-green-700 dark:text-green-400">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
          </svg>
          <span className="font-medium">{submitResult?.message || '已提交'}</span>
        </div>
      </div>
    );
  }

  if (card.status === 'cancelled') {
    return (
      <div className="mt-3 p-4 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-tertiary)]">
        <span className="text-[var(--text-tertiary)]">已取消</span>
      </div>
    );
  }

  // Show error if submit failed
  if (submitResult && !submitResult.success) {
    return (
      <div className="mt-3 p-4 rounded-xl border border-red-200 bg-red-50 dark:border-red-800 dark:bg-red-900/20">
        <div className="flex items-center gap-2 text-red-700 dark:text-red-400 mb-3">
          <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
          </svg>
          <span className="font-medium">{submitResult.message}</span>
        </div>
        <button
          onClick={() => setSubmitResult(null)}
          className="text-sm text-[var(--accent-primary)] hover:underline"
        >
          重试
        </button>
      </div>
    );
  }

  // Render form based on type
  const title = card.type === 'maintenance' ? '添加设备维护计划' : '添加假期';
  const icon = card.type === 'maintenance' ? (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  ) : (
    <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
      <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 7V3m8 4V3m-9 8h10M5 21h14a2 2 0 002-2V7a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z" />
    </svg>
  );

  return (
    <div className="mt-3 p-4 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)]">
      <div className="flex items-center gap-2 mb-4 text-[var(--text-primary)]">
        {icon}
        <h3 className="font-medium">{title}</h3>
      </div>

      {/* Manage existing downtime entries */}
      <div className="mb-4 p-3 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)]">
        <div className="flex items-center justify-between gap-2 mb-2">
          <span className="text-sm font-medium text-[var(--text-primary)]">当前已有</span>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={refreshDowntime}
              className="text-xs px-2 py-1 rounded border border-[var(--border-primary)] hover:bg-[var(--bg-hover)] transition-colors"
            >
              刷新
            </button>
            <button
              type="button"
              onClick={handleDeleteAll}
              className="text-xs px-2 py-1 rounded border border-red-500/30 text-red-500 hover:bg-red-500/10 transition-colors"
              disabled={
                !downtime ||
                (card.type === 'holiday' ? downtime.holidays.length === 0 : downtime.maintenance.length === 0)
              }
              title="删除全部（谨慎）"
            >
              删除全部
            </button>
          </div>
        </div>

        {manageMsg && <div className="text-xs text-[var(--text-secondary)] mb-2">{manageMsg}</div>}

        {!downtime ? (
          <div className="text-xs text-[var(--text-tertiary)]">加载中…</div>
        ) : card.type === 'holiday' ? (
          downtime.holidays.length === 0 ? (
            <div className="text-xs text-[var(--text-tertiary)]">暂无假期</div>
          ) : (
            <div className="space-y-1">
              {downtime.holidays.map((h, idx) => (
                <div
                  key={`${h.start}-${h.end}-${idx}`}
                  className="flex items-center justify-between gap-2"
                >
                  <div className="text-xs text-[var(--text-secondary)] truncate">
                    [{idx}] {h.name}（{h.start} ~ {h.end}）
                  </div>
                  <button
                    type="button"
                    onClick={() => handleDeleteHoliday(idx)}
                    className="text-xs px-2 py-1 rounded border border-red-500/30 text-red-500 hover:bg-red-500/10 transition-colors"
                  >
                    删除
                  </button>
                </div>
              ))}
            </div>
          )
        ) : downtime.maintenance.length === 0 ? (
          <div className="text-xs text-[var(--text-tertiary)]">暂无维护计划</div>
        ) : (
          <div className="space-y-1">
            {downtime.maintenance.map((m, idx) => (
              <div
                key={`${m.machine_id}-${m.start}-${idx}`}
                className="flex items-center justify-between gap-2"
              >
                <div className="text-xs text-[var(--text-secondary)] truncate">
                  [{idx}] {m.machine_id} {m.reason || ''}（{m.start} ~ {m.end}）
                </div>
                <button
                  type="button"
                  onClick={() => handleDeleteMaintenance(idx)}
                  className="text-xs px-2 py-1 rounded border border-red-500/30 text-red-500 hover:bg-red-500/10 transition-colors"
                >
                  删除
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {card.type === 'maintenance' ? (
        <MaintenanceForm
          onSubmit={handleMaintenanceSubmit}
          onCancel={handleCancel}
          isSubmitting={isSubmitting}
        />
      ) : (
        <HolidayForm
          onSubmit={handleHolidaySubmit}
          onCancel={handleCancel}
          isSubmitting={isSubmitting}
        />
      )}
    </div>
  );
}
