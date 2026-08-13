'use client';

import { useState, useRef, useCallback } from 'react';

interface ResizableDividerProps {
  onResize: (percentage: number) => void;
  direction?: 'horizontal' | 'vertical';
}

export function ResizableDivider({
  onResize,
  direction = 'horizontal',
}: ResizableDividerProps) {
  const [isDragging, setIsDragging] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);

  const handleMouseDown = useCallback((e: React.MouseEvent) => {
    e.preventDefault();
    setIsDragging(true);

    const handleMouseMove = (e: MouseEvent) => {
      if (!containerRef.current) return;

      const container = containerRef.current.parentElement;
      if (!container) return;

      const rect = container.getBoundingClientRect();

      if (direction === 'horizontal') {
        const percentage = ((e.clientY - rect.top) / rect.height) * 100;
        onResize(Math.max(20, Math.min(80, percentage)));
      } else {
        const percentage = ((e.clientX - rect.left) / rect.width) * 100;
        onResize(Math.max(20, Math.min(80, percentage)));
      }
    };

    const handleMouseUp = () => {
      setIsDragging(false);
      document.removeEventListener('mousemove', handleMouseMove);
      document.removeEventListener('mouseup', handleMouseUp);
    };

    document.addEventListener('mousemove', handleMouseMove);
    document.addEventListener('mouseup', handleMouseUp);
  }, [direction, onResize]);

  return (
    <div
      ref={containerRef}
      className={`
        group flex-shrink-0
        ${direction === 'horizontal'
          ? 'h-2 cursor-row-resize hover:bg-[var(--border-primary)]'
          : 'w-2 cursor-col-resize hover:bg-[var(--border-primary)]'
        }
        ${isDragging ? 'bg-[var(--accent-primary)]' : 'bg-transparent'}
        transition-colors
      `}
      onMouseDown={handleMouseDown}
    >
      <div
        className={`
          ${direction === 'horizontal'
            ? 'h-px w-full bg-[var(--border-primary)] group-hover:bg-[var(--accent-primary)]'
            : 'w-px h-full bg-[var(--border-primary)] group-hover:bg-[var(--accent-primary)]'
          }
          ${isDragging ? 'bg-[var(--accent-primary)]' : ''}
          transition-colors
        `}
      />
    </div>
  );
}
