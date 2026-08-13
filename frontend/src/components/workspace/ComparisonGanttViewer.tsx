'use client';

import { useState, useEffect } from 'react';

interface ComparisonGanttViewerProps {
  apiUrl?: string;
}

interface ComparisonStatus {
  available: boolean;
  timestamp: string | null;
}

export function ComparisonGanttViewer({ apiUrl = 'http://localhost:8000' }: ComparisonGanttViewerProps) {
  const [status, setStatus] = useState<ComparisonStatus | null>(null);
  const [refreshKey, setRefreshKey] = useState(0);
  const [lastTimestamp, setLastTimestamp] = useState<string | null>(null);

  useEffect(() => {
    const checkStatus = async () => {
      try {
        const res = await fetch(`${apiUrl}/api/schedule/comparison/status`);
        if (res.ok) {
          const data: ComparisonStatus = await res.json();
          setStatus(data);
          if (data.timestamp && data.timestamp !== lastTimestamp) {
            setLastTimestamp(data.timestamp);
            setRefreshKey(prev => prev + 1);
          }
        }
      } catch {}
    };
    checkStatus();
    const interval = setInterval(checkStatus, 3000);
    return () => clearInterval(interval);
  }, [apiUrl, lastTimestamp]);

  if (!status?.available) {
    return (
      <div className="h-full w-full flex items-center justify-center rounded-lg border border-dashed border-[var(--border-primary)] bg-[var(--bg-secondary)]">
        <span className="text-[var(--text-tertiary)] text-sm">重排后的排产计划将显示在此处</span>
      </div>
    );
  }

  return (
    <div className="flex flex-col h-full bg-[var(--gantt-bg)] rounded-lg border border-[var(--border-primary)] overflow-hidden">
      <div className="flex items-center px-4 py-2 border-b border-[var(--border-primary)] bg-[var(--bg-secondary)]">
        <span className="text-sm font-medium text-[var(--text-primary)]">重排甘特图</span>
      </div>
      <div className="flex-1 relative">
        <iframe
          key={refreshKey}
          src={`${apiUrl}/api/schedule/gantt/comparison`}
          className="absolute inset-0 w-full h-full border-0"
          title="Comparison Gantt Chart"
        />
      </div>
    </div>
  );
}
