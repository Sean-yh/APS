'use client';

import { useEffect, useId, useMemo, useState } from 'react';
import { ConstraintsPanel } from '@/components/panels/ConstraintsPanel';

type UiStatus = {
  downtime_calendar?: {
    holidays?: unknown[];
    maintenance?: unknown[];
  };
  overrides?: {
    containers?: unknown[];
    orders?: unknown[];
  };
  current_schedule?: {
    meta?: {
      applied_constraints?: unknown;
    };
  };
};

function formatCount(v: number) {
  return v > 99 ? '99+' : String(v);
}

function countAppliedConstraints(v: unknown) {
  if (!v) return 0;
  if (Array.isArray(v)) return v.length;
  if (typeof v === 'object') return Object.keys(v as Record<string, unknown>).length;
  return 1;
}

export function ConstraintsChip({ apiUrl }: { apiUrl: string }) {
  const [open, setOpen] = useState(false);
  const dialogId = useId();
  const [status, setStatus] = useState<UiStatus | null>(null);

  useEffect(() => {
    let mounted = true;
    let timer: ReturnType<typeof setInterval> | null = null;

    async function refresh() {
      try {
        const res = await fetch(`${apiUrl}/api/ui/status`, { cache: 'no-store' });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = (await res.json()) as UiStatus;
        if (mounted) setStatus(data);
      } catch {
        // Keep prior status if any; chip still works as an entry point.
      }
    }

    refresh();
    timer = setInterval(refresh, 5000);

    return () => {
      mounted = false;
      if (timer) clearInterval(timer);
    };
  }, [apiUrl]);

  const constraintsCount = useMemo(() => {
    const holidays = status?.downtime_calendar?.holidays?.length ?? 0;
    const maintenance = status?.downtime_calendar?.maintenance?.length ?? 0;
    const containerOverrides = status?.overrides?.containers?.length ?? 0;
    const orderOverrides = status?.overrides?.orders?.length ?? 0;
    const forcedConstraints = countAppliedConstraints(
      status?.current_schedule?.meta?.applied_constraints
    );
    return holidays + maintenance + containerOverrides + orderOverrides + forcedConstraints;
  }, [status]);

  const constraintsCountLabel = status ? formatCount(constraintsCount) : '…';

  useEffect(() => {
    if (!open) return;

    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', onKeyDown);

    return () => {
      document.body.style.overflow = prevOverflow;
      window.removeEventListener('keydown', onKeyDown);
    };
  }, [open]);

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={dialogId}
        className="inline-flex items-center gap-2 rounded-full border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-3 py-1.5 text-sm font-medium text-[var(--text-primary)] hover:bg-[var(--bg-hover)] transition-colors"
      >
        <span>排程依据</span>
        <span
          data-testid="constraints-count"
          className="min-w-7 rounded-full bg-[var(--bg-tertiary)] px-2 py-0.5 text-xs text-[var(--text-secondary)] text-center tabular-nums"
          title="生效项数量"
        >
          {constraintsCountLabel}
        </span>
      </button>

      {open && (
        <div className="fixed inset-0 z-50">
          <button
            type="button"
            data-testid="constraints-drawer-backdrop"
            className="absolute inset-0 bg-black/30"
            onClick={() => setOpen(false)}
            aria-label="关闭排程依据面板"
          />

          <div
            id={dialogId}
            role="dialog"
            aria-label="排程依据"
            aria-modal="true"
            className="absolute right-0 top-0 h-full w-[440px] max-w-[92vw] bg-[var(--bg-primary)] border-l border-[var(--border-primary)] shadow-xl flex flex-col"
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-[var(--border-primary)] bg-[var(--bg-secondary)]">
              <div className="min-w-0">
                <div className="text-sm font-medium text-[var(--text-primary)]">
                  排程依据
                </div>
                <div className="text-xs text-[var(--text-tertiary)] truncate">
                  ERP/停机/现场/强制规则
                </div>
              </div>
              <div className="flex items-center gap-2">
                <span className="rounded-full bg-[var(--bg-tertiary)] px-2 py-0.5 text-xs text-[var(--text-secondary)] tabular-nums">
                  {constraintsCountLabel}
                </span>
              <button
                type="button"
                onClick={() => setOpen(false)}
                className="p-1.5 rounded-lg hover:bg-[var(--bg-hover)] transition-colors"
                aria-label="关闭"
              >
                <svg
                  className="w-5 h-5 text-[var(--text-secondary)]"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M6 18L18 6M6 6l12 12"
                  />
                </svg>
              </button>
              </div>
            </div>

            <div className="flex-1 overflow-y-auto p-4">
              <ConstraintsPanel apiUrl={apiUrl} />
            </div>
          </div>
        </div>
      )}
    </>
  );
}
