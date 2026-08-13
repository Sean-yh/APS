'use client';

import { useEffect, useState } from 'react';

type UiStatus = {
  timestamp: string;
  erp_snapshot: {
    orders: { path: string; exists: boolean; timestamp?: string; count?: number };
    inventory: { path: string; exists: boolean; timestamp?: string; count?: number };
  };
  overrides?: { containers: string[]; orders: string[] };
  downtime_calendar: {
    holidays: Array<{ name: string; start: string; end: string }>;
    maintenance: Array<{ machine_id: string; reason: string; start: string; end: string }>;
  };
  production_context: {
    confirmed: boolean;
    forming_states?: Record<string, string> | null;
    setup_remaining_by_machine?: Record<string, number> | null;
    checked_at?: string | null;
  };
  current_schedule: {
    exists: boolean;
    meta: {
      line?: string;
      start_time?: string;
      horizon_h?: number;
      applied_constraints?: unknown;
    };
    applied_downtime: boolean;
    downtime_block_counts: { holiday: number; maintenance: number };
  };
  comparisons: {
    count: number;
    schedules: Array<{ id: string; label: string; timestamp: string; constraint?: Record<string, unknown> }>;
  };
};

function fmtTs(v?: string | null) {
  if (!v) return 'N/A';
  // Keep it simple; backend returns ISO. UI can be upgraded later.
  const s = v.replace('T', ' ').replace('Z', '');
  // Drop fractional seconds to keep the UI scannable.
  return s.split('.')[0];
}

function formatCount(v: number) {
  return v > 99 ? '99+' : String(v);
}

function countAppliedConstraints(v: unknown) {
  if (!v) return 0;
  if (Array.isArray(v)) return v.length;
  if (typeof v === 'object') return Object.keys(v as Record<string, unknown>).length;
  return 1;
}

function Badge({
  tone = 'neutral',
  children,
}: {
  tone?: 'neutral' | 'good' | 'warn' | 'bad';
  children: React.ReactNode;
}) {
  const cls =
    tone === 'good'
      ? 'bg-green-500/15 text-green-600'
      : tone === 'warn'
        ? 'bg-amber-500/15 text-amber-600'
        : tone === 'bad'
          ? 'bg-red-500/15 text-red-600'
          : 'bg-[var(--bg-tertiary)] text-[var(--text-secondary)]';
  return (
    <span className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs tabular-nums ${cls}`}>
      {children}
    </span>
  );
}

function SegmentedControl({
  value,
  onChange,
  options,
}: {
  value: string;
  onChange: (v: string) => void;
  options: Array<{ value: string; label: string }>;
}) {
  return (
    <div className="inline-flex rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] p-0.5">
      {options.map((opt) => {
        const active = opt.value === value;
        return (
          <button
            key={opt.value}
            type="button"
            aria-pressed={active}
            onClick={() => onChange(opt.value)}
            className={[
              'px-2.5 py-1 text-xs rounded-md transition-colors',
              active
                ? 'bg-[var(--bg-secondary)] text-[var(--text-primary)]'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)]',
            ].join(' ')}
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function CollapsibleCard({
  title,
  description,
  status,
  meta,
  defaultOpen = false,
  children,
}: {
  title: string;
  description?: React.ReactNode;
  status?: React.ReactNode;
  meta?: React.ReactNode;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)] overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="w-full flex items-center justify-between gap-3 px-3 py-2 bg-[var(--bg-tertiary)] hover:bg-[var(--bg-hover)] transition-colors"
      >
        <div className="min-w-0 flex-1">
          <div className="min-w-0 flex items-center gap-2">
            <span className="text-sm font-medium text-[var(--text-primary)] truncate">{title}</span>
            {status ? <span className="shrink-0">{status}</span> : null}
          </div>
          {description ? (
            <div className="mt-0.5 text-xs text-[var(--text-tertiary)] leading-snug">{description}</div>
          ) : null}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {meta ? (
            <div className="text-xs text-[var(--text-tertiary)] tabular-nums text-right whitespace-nowrap">
              {meta}
            </div>
          ) : null}
          <svg
            className={`w-4 h-4 text-[var(--text-secondary)] transition-transform ${open ? 'rotate-180' : ''}`}
            fill="none"
            stroke="currentColor"
            viewBox="0 0 24 24"
          >
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
          </svg>
        </div>
      </button>
      {open ? <div className="px-3 py-2">{children}</div> : null}
    </div>
  );
}

export function ConstraintsPanel({ apiUrl = 'http://localhost:8000' }: { apiUrl?: string }) {
  const [status, setStatus] = useState<UiStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showRawConstraints, setShowRawConstraints] = useState(false);
  const [view, setView] = useState<'overview' | 'details'>('overview');

  useEffect(() => {
    let mounted = true;
    let timer: ReturnType<typeof setInterval> | null = null;

    async function refresh() {
      try {
        const res = await fetch(`${apiUrl}/api/ui/status`, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as UiStatus;
        if (mounted) {
          setStatus(data);
          setError(null);
        }
      } catch (e) {
        if (mounted) setError(e instanceof Error ? e.message : 'Unknown error');
      }
    }

    refresh();
    timer = setInterval(refresh, 5000);

    return () => {
      mounted = false;
      if (timer) clearInterval(timer);
    };
  }, [apiUrl]);

  // Reset raw expansion when refreshed to avoid leaving a giant JSON blob open forever.
  useEffect(() => {
    setShowRawConstraints(false);
  }, [status?.timestamp]);

  if (error) {
    return <div className="text-sm text-red-400">Failed to load: {error}</div>;
  }

  if (!status) {
    return <div className="text-sm text-[var(--text-tertiary)]">Loading…</div>;
  }

  const holidays = status.downtime_calendar?.holidays || [];
  const maintenance = status.downtime_calendar?.maintenance || [];
  const comparisons = status.comparisons?.schedules || [];
  const formingStates = status.production_context.forming_states || {};
  const setupRemaining = status.production_context.setup_remaining_by_machine || {};
  const containerOverrides = status.overrides?.containers?.length ?? 0;
  const orderOverrides = status.overrides?.orders?.length ?? 0;
  const pc = status.production_context;
  const pcTone = pc.confirmed ? 'good' : 'bad';

  const appliedConstraints = status.current_schedule?.meta?.applied_constraints;
  const appliedConstraintsCount = countAppliedConstraints(appliedConstraints);
  const totalConstraints = holidays.length + maintenance.length + containerOverrides + orderOverrides + appliedConstraintsCount;

  const appliedConstraintsObj =
    appliedConstraints && typeof appliedConstraints === 'object'
      ? (appliedConstraints as Record<string, unknown>)
      : null;
  const appliedLabel = appliedConstraintsObj?.label ? String(appliedConstraintsObj.label) : null;
  const appliedSource = appliedConstraintsObj?.source_schedule_id
    ? String(appliedConstraintsObj.source_schedule_id)
    : null;

  const isDetails = view === 'details';
  const erpOk = Boolean(status.erp_snapshot.orders.exists && status.erp_snapshot.inventory.exists);
  const readyTone = !erpOk ? 'bad' : pc.confirmed ? 'good' : 'warn';
  const readyLabel = !erpOk ? 'ERP 数据缺失' : pc.confirmed ? '排程就绪' : '等待确认';

  return (
    <div className="space-y-3 text-sm">
      <div className="flex items-center justify-between gap-2">
        <div className="text-xs text-[var(--text-tertiary)]">更新于: {fmtTs(status.timestamp)}</div>
        <div className="flex items-center gap-2">
          <Badge tone={readyTone}>{readyLabel}</Badge>
          <Badge>{formatCount(totalConstraints)} 条约束</Badge>
        </div>
      </div>

      <div className="flex items-center justify-between gap-2">
        <SegmentedControl
          value={view}
          onChange={(v) => setView(v as 'overview' | 'details')}
          options={[
            { value: 'overview', label: '概览' },
            { value: 'details', label: '详情' },
          ]}
        />
        <div className="text-xs text-[var(--text-tertiary)]">{isDetails ? '默认展开更多细节' : '点击条目查看细节'}</div>
      </div>

      <CollapsibleCard
        key={`${view}-erp`}
        title="ERP 快照"
        description="本次排程的 ERP 数据基准（用于保证结果可追溯）"
        status={<Badge tone={erpOk ? 'good' : 'bad'}>{erpOk ? '可用' : '缺失'}</Badge>}
        meta={`${status.erp_snapshot.orders.count ?? '—'} 订单 · ${status.erp_snapshot.inventory.count ?? '—'} 库存`}
        defaultOpen={isDetails}
      >
        <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-[var(--text-secondary)]">
          <div className="text-[var(--text-tertiary)]">订单</div>
          <div className="text-right">
            {status.erp_snapshot.orders.count ?? '—'} @ {fmtTs(status.erp_snapshot.orders.timestamp)}
          </div>
          <div className="text-[var(--text-tertiary)]">库存</div>
          <div className="text-right">
            {status.erp_snapshot.inventory.count ?? '—'} @ {fmtTs(status.erp_snapshot.inventory.timestamp)}
          </div>
        </div>
      </CollapsibleCard>

      <CollapsibleCard
        key={`${view}-downtime`}
        title="停机日历"
        description="节假日/维护等不可用时间（硬约束：不可排产）"
        status={
          holidays.length + maintenance.length === 0 ? (
            <Badge tone="good">无停机</Badge>
          ) : status.current_schedule.applied_downtime ? (
            <Badge tone="good">已应用</Badge>
          ) : (
            <Badge tone="warn">未应用</Badge>
          )
        }
        meta={`${holidays.length} 节假日 · ${maintenance.length} 维护`}
        defaultOpen={isDetails}
      >
        <div className="space-y-2 text-[var(--text-secondary)]">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[var(--text-tertiary)]">日历</div>
            <div className="text-right">
              {holidays.length} 节假日，{maintenance.length} 维护
            </div>
          </div>
          <div className="flex items-center justify-between gap-2">
            <div className="text-[var(--text-tertiary)]">当前甘特图已应用</div>
            <div className="text-right">
              {status.current_schedule.applied_downtime ? '是' : '否'}（节假日{' '}
              {status.current_schedule.downtime_block_counts.holiday}，维护{' '}
              {status.current_schedule.downtime_block_counts.maintenance}）
            </div>
          </div>
        </div>
      </CollapsibleCard>

      <CollapsibleCard
        key={`${view}-overrides`}
        title="人工覆盖"
        description="人工锁定/禁止/强制规则（硬约束：不可违反）"
        status={
          containerOverrides + orderOverrides > 0 ? <Badge tone="warn">已覆盖</Badge> : <Badge tone="good">无</Badge>
        }
        meta={`${containerOverrides} 资源 · ${orderOverrides} 订单`}
        defaultOpen={isDetails}
      >
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[var(--text-tertiary)]">更新于</div>
            <div className="text-right text-[var(--text-secondary)]">{fmtTs(status.timestamp)}</div>
          </div>
          {status.overrides?.containers?.length ? (
            <div className="flex flex-wrap gap-1">
              {status.overrides.containers.slice(0, 12).map((id) => (
                <span
                  key={id}
                  className="rounded bg-[var(--bg-tertiary)] px-2 py-0.5 text-xs text-[var(--text-secondary)]"
                >
                  {id}
                </span>
              ))}
              {status.overrides.containers.length > 12 ? (
                <span className="rounded bg-[var(--bg-tertiary)] px-2 py-0.5 text-xs text-[var(--text-tertiary)]">
                  +{status.overrides.containers.length - 12}
                </span>
              ) : null}
            </div>
          ) : (
            <div className="text-xs text-[var(--text-tertiary)]">无人工覆盖。</div>
          )}
        </div>
      </CollapsibleCard>

      <CollapsibleCard
        key={`${view}-pc`}
        title="生产上下文"
        description="现场状态/配置输入；未确认会降低排程可信度"
        status={<Badge tone={pcTone}>{pc.confirmed ? '已确认' : '未确认'}</Badge>}
        meta={<span className="text-[var(--text-tertiary)]">{pc.checked_at ? fmtTs(pc.checked_at) : '—'}</span>}
        defaultOpen={isDetails}
      >
        <div className="space-y-2">
          <div className="flex items-center justify-between gap-2">
            <div className="text-[var(--text-tertiary)]">状态</div>
            <div className="flex items-center gap-2">
              <Badge tone={pcTone}>{pc.confirmed ? '已确认' : '未确认'}</Badge>
            </div>
          </div>

          {pc.checked_at ? (
            <div className="flex items-center justify-between gap-2">
              <div className="text-[var(--text-tertiary)]">检查时间</div>
              <div className="text-right text-[var(--text-secondary)]">
                {fmtTs(pc.checked_at)}
              </div>
            </div>
          ) : null}

          {Object.keys(formingStates).length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {Object.entries(formingStates)
                .sort(([a], [b]) => a.localeCompare(b))
                .map(([m, s]) => {
                  const rem = setupRemaining[m];
                  const extra = s === 'setup' && typeof rem === 'number' ? ` (${rem}h)` : '';
                  return (
                    <span
                      key={m}
                      className="rounded bg-[var(--bg-tertiary)] px-2 py-0.5 text-xs text-[var(--text-secondary)]"
                      title={m}
                    >
                      {m}: {s}
                      {extra}
                    </span>
                  );
                })}
            </div>
          ) : (
            <div className="text-xs text-[var(--text-tertiary)]">暂无设备上下文。</div>
          )}
        </div>
      </CollapsibleCard>

      <CollapsibleCard
        key={`${view}-forced`}
        title="强制约束集"
        description="来自场景/对比排程的强制规则（会改变排程结果）"
        status={appliedConstraints ? <Badge tone="warn">已启用</Badge> : <Badge tone="good">无</Badge>}
        meta={
          appliedConstraints
            ? `${appliedConstraintsCount} 已应用${appliedLabel ? ` · ${appliedLabel}` : ''}`
            : '无'
        }
        defaultOpen={false}
      >
        <div className="space-y-2">
          {appliedConstraints ? (
            <div className="space-y-1 text-[var(--text-secondary)]">
              <div className="flex items-center justify-between gap-2">
                <div className="text-[var(--text-tertiary)]">已应用</div>
                <div className="text-right">是</div>
              </div>
              {appliedSource ? (
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[var(--text-tertiary)]">来源</div>
                  <div className="text-right">{appliedSource}</div>
                </div>
              ) : null}
              {appliedLabel ? (
                <div className="flex items-center justify-between gap-2">
                  <div className="text-[var(--text-tertiary)]">标签</div>
                  <div className="text-right">{appliedLabel}</div>
                </div>
              ) : null}
            </div>
          ) : (
            <div className="text-xs text-[var(--text-tertiary)]">未应用强制约束。</div>
          )}

          {appliedConstraints ? (
            <div className="flex items-center justify-between gap-2">
              <button
                type="button"
                className="text-xs text-[var(--text-secondary)] hover:text-[var(--text-primary)] underline underline-offset-2"
                onClick={() => setShowRawConstraints((v) => !v)}
              >
                查看原始 JSON
              </button>
              {comparisons.length ? (
                <Badge>{formatCount(comparisons.length)} 个对比</Badge>
              ) : (
                <span className="text-xs text-[var(--text-tertiary)]">0 个对比</span>
              )}
            </div>
          ) : (
            <div className="text-xs text-[var(--text-tertiary)]">对比排程: {comparisons.length}</div>
          )}

          {showRawConstraints && appliedConstraints ? (
            <pre className="text-xs whitespace-pre-wrap rounded bg-[var(--bg-tertiary)] p-2 text-[var(--text-secondary)] overflow-x-auto">
              {JSON.stringify(appliedConstraints, null, 2)}
            </pre>
          ) : null}

          {comparisons.length > 0 ? (
            <div className="space-y-1">
              {comparisons.slice(0, 5).map((c) => (
                <div key={c.id} className="text-[var(--text-secondary)]">
                  {c.label} ({c.id}) {c.constraint ? `— ${JSON.stringify(c.constraint)}` : ''}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-xs text-[var(--text-tertiary)]">暂无活动场景约束。</div>
          )}
        </div>
      </CollapsibleCard>
    </div>
  );
}
