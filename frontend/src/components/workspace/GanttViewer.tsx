'use client';

interface GanttViewerProps {
  apiUrl?: string;
}

export function GanttViewer({ apiUrl = 'http://localhost:8000' }: GanttViewerProps) {
  return (
    <div className="flex flex-col h-full bg-[var(--gantt-bg)] rounded-lg border border-[var(--border-primary)] overflow-hidden">
      <div className="flex items-center px-4 py-2 border-b border-[var(--border-primary)] bg-[var(--bg-secondary)]">
        <span className="text-sm font-medium text-[var(--text-primary)]">排产甘特图</span>
      </div>
      <div className="flex-1 relative">
        <iframe
          src={`${apiUrl}/api/schedule/gantt`}
          className="absolute inset-0 w-full h-full border-0"
          title="Gantt Chart"
        />
      </div>
    </div>
  );
}
