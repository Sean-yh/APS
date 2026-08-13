'use client';

import { ThreeColumnLayout } from '@/components/layout/ThreeColumnLayout';
import { LeftPanel } from '@/components/layout/LeftPanel';
import { WorkspaceArea } from '@/components/workspace/WorkspaceArea';
import { ChatArea } from '@/components/chat/ChatArea';
import { WorkspaceProvider, useWorkspaceStore } from '@/stores/workspaceStore';
import { ChartIcon } from '@/components/ui/Icons';

// 内部组件，使用 workspace store
function HomeContent({ apiUrl }: { apiUrl: string }) {
  const { state, openSchedule } = useWorkspaceStore();

  return (
    <>
      {/* Desktop-only: edge handle to open the workspace panel (schedule) */}
      {!state.showWorkspace && (
        <>
          <button
            type="button"
            onClick={() => openSchedule('current')}
            className="hidden lg:flex fixed right-0 top-4 z-50 items-center gap-2 px-3 py-2 rounded-l-xl border border-r-0 border-[var(--border-primary)] bg-[var(--accent-primary)] hover:bg-[var(--accent-hover)] text-white shadow-sm focus:outline-none focus:ring-2 focus:ring-[var(--accent-primary)] focus:ring-offset-2 focus:ring-offset-[var(--bg-primary)]"
            title="打开排产方案"
          >
            <ChartIcon className="w-4 h-4 text-white/90" />
            <span className="text-sm font-medium">排产</span>
          </button>

          {/* Mobile: workspace panel is hidden, so show a non-interactive hint instead. */}
          <div className="lg:hidden fixed right-0 top-4 z-50 flex items-center gap-2 px-3 py-2 rounded-l-xl border border-r-0 border-[var(--border-primary)] bg-[var(--bg-secondary)] text-[var(--text-tertiary)]">
            <ChartIcon className="w-4 h-4 text-[var(--text-tertiary)]" />
            <span className="text-xs font-medium">排产（Desktop only）</span>
          </div>
        </>
      )}

      <ThreeColumnLayout
        leftPanel={<LeftPanel />}
        chatArea={<ChatArea apiUrl={apiUrl} />}
        workspaceArea={<WorkspaceArea apiUrl={apiUrl} />}
        workspaceVisible={state.showWorkspace}
      />
    </>
  );
}

export default function Home() {
  // Railway: set NEXT_PUBLIC_API_URL to the backend service URL.
  // Local dev fallback keeps the current workflow working.
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  return (
    <WorkspaceProvider apiUrl={apiUrl}>
      <HomeContent apiUrl={apiUrl} />
    </WorkspaceProvider>
  );
}
