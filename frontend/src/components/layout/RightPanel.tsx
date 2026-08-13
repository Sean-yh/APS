'use client';

import { useState } from 'react';
import { ProgressPanel } from '@/components/panels/ProgressPanel';
import { ArtifactsPanel } from '@/components/panels/ArtifactsPanel';
import { ContextPanel } from '@/components/panels/ContextPanel';
import { ConstraintsPanel } from '@/components/panels/ConstraintsPanel';

interface CollapsibleSectionProps {
  title: string;
  icon: React.ReactNode;
  children: React.ReactNode;
  defaultOpen?: boolean;
}

function CollapsibleSection({
  title,
  icon,
  children,
  defaultOpen = true,
}: CollapsibleSectionProps) {
  const [isOpen, setIsOpen] = useState(defaultOpen);

  return (
    <div className="border-b border-[var(--border-primary)]">
      <button
        onClick={() => setIsOpen(!isOpen)}
        className="w-full px-4 py-3 flex items-center gap-2 hover:bg-[var(--bg-hover)] transition-colors"
      >
        <svg
          className={`w-4 h-4 text-[var(--text-tertiary)] transition-transform ${
            isOpen ? 'rotate-90' : ''
          }`}
          fill="none"
          stroke="currentColor"
          viewBox="0 0 24 24"
        >
          <path
            strokeLinecap="round"
            strokeLinejoin="round"
            strokeWidth={2}
            d="M9 5l7 7-7 7"
          />
        </svg>
        <span className="flex items-center gap-2 text-sm font-medium text-[var(--text-primary)]">
          {icon}
          {title}
        </span>
      </button>
      {isOpen && <div className="px-4 pb-3">{children}</div>}
    </div>
  );
}

export function RightPanel() {
  return (
    <div className="flex flex-col h-full overflow-y-auto">
      {/* Progress Section */}
      <CollapsibleSection
        title="Progress"
        icon={
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2m-6 9l2 2 4-4"
            />
          </svg>
        }
      >
        <ProgressPanel />
      </CollapsibleSection>

      {/* Artifacts Section */}
      <CollapsibleSection
        title="Artifacts"
        icon={
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
            />
          </svg>
        }
        defaultOpen={false}
      >
        <ArtifactsPanel />
      </CollapsibleSection>

      {/* Constraints Section */}
      <CollapsibleSection
        title="排程依据"
        icon={
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"
            />
          </svg>
        }
        defaultOpen={false}
      >
        <ConstraintsPanel />
      </CollapsibleSection>

      {/* Context Section */}
      <CollapsibleSection
        title="Context"
        icon={
          <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth={2}
              d="M4 7v10c0 2.21 3.582 4 8 4s8-1.79 8-4V7M4 7c0 2.21 3.582 4 8 4s8-1.79 8-4M4 7c0-2.21 3.582-4 8-4s8 1.79 8 4"
            />
          </svg>
        }
        defaultOpen={false}
      >
        <ContextPanel />
      </CollapsibleSection>
    </div>
  );
}
