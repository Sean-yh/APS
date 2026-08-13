'use client';

import { useState } from 'react';
import { ConstraintsPanel } from '@/components/panels/ConstraintsPanel';

export function ConstraintsCard({ apiUrl }: { apiUrl: string }) {
  const [collapsed, setCollapsed] = useState(true);

  return (
    <div className="bg-[var(--bg-secondary)] rounded-lg border border-[var(--border-primary)] overflow-hidden">
      <button
        onClick={() => setCollapsed((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 border-b border-[var(--border-primary)] bg-[var(--bg-tertiary)] hover:bg-[var(--bg-hover)] transition-colors"
        title={collapsed ? '展开' : '折叠'}
      >
        <div className="flex items-center gap-2">
          <span className="font-medium text-sm text-[var(--text-primary)]">排程依据（实时）</span>
          <span className="text-xs text-[var(--text-tertiary)]">
            ERP/停机/现场/强制规则
          </span>
        </div>
        <svg
          className={`w-4 h-4 text-[var(--text-secondary)] transition-transform ${collapsed ? '-rotate-90' : ''}`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
        </svg>
      </button>

      {!collapsed && (
        <div className="px-3 py-2">
          <ConstraintsPanel apiUrl={apiUrl} />
        </div>
      )}
    </div>
  );
}
