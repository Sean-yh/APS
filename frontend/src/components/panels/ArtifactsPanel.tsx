'use client';

import { useChatStore } from '@/stores/chatStore';

interface Artifact {
  id: string;
  name: string;
  type: 'chart' | 'json' | 'html';
  icon: string;
}

export function ArtifactsPanel() {
  const { activeSession } = useChatStore();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

  const getArtifacts = (): Artifact[] => {
    if (!activeSession) {
      return [];
    }

    const artifacts: Artifact[] = [];
    const messages = activeSession.messages;

    const hasGantt = messages.some(m =>
      m.toolCalls?.some(t =>
        t.name.includes('gantt') ||
        t.output?.includes('gantt') ||
        t.output?.includes('甘特图')
      )
    );

    if (hasGantt) {
      artifacts.push({
        id: 'gantt',
        name: 'gantt.html',
        type: 'html',
        icon: '📊',
      });
    }

    const hasSchedule = messages.some(m =>
      m.toolCalls?.some(t =>
        t.name.includes('schedule') ||
        t.output?.includes('schedule')
      )
    );

    if (hasSchedule) {
      artifacts.push({
        id: 'schedule',
        name: 'schedule.json',
        type: 'json',
        icon: '📄',
      });
    }

    return artifacts;
  };

  const artifacts = getArtifacts();

  if (artifacts.length === 0) {
    return (
      <div className="text-sm text-[var(--text-tertiary)] py-2">
        暂无生成的文件
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {artifacts.map((artifact) => (
        <a
          key={artifact.id}
          href={artifact.id === 'gantt' ? `${apiUrl}/api/schedule/gantt` : '#'}
          target="_blank"
          rel="noopener noreferrer"
          className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-[var(--bg-hover)] transition-colors cursor-pointer"
        >
          <span>{artifact.icon}</span>
          <span className="text-sm text-[var(--text-primary)]">{artifact.name}</span>
        </a>
      ))}
    </div>
  );
}
