'use client';

import { useState, useEffect } from 'react';
import type { ErpExportCard as ErpExportCardType } from '@/types/chat';

interface ErpExportCardProps {
  card: ErpExportCardType;
}

export function ErpExportCard({ card }: ErpExportCardProps) {
  const [phase, setPhase] = useState<'sending' | 'success'>('sending');

  useEffect(() => {
    // 显示发送动画 1.5 秒后切换到成功状态
    const timer = setTimeout(() => {
      setPhase('success');
    }, 1500);

    return () => clearTimeout(timer);
  }, []);

  return (
    <div className="mt-4 p-4 bg-[var(--card-bg)] rounded-xl border border-[var(--border-primary)] shadow-sm overflow-hidden">
      {/* 发送中阶段 */}
      {phase === 'sending' && (
        <div className="flex flex-col items-center py-6">
          {/* 文件飞出动画 */}
          <div className="relative w-24 h-24 mb-4">
            {/* 底部文件图标 */}
            <div className="absolute inset-0 flex items-center justify-center">
              <svg
                className="w-12 h-12 text-[var(--text-tertiary)]"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
            </div>

            {/* 飞出的文件动画 */}
            <div className="absolute inset-0 flex items-center justify-center animate-fly-out">
              <svg
                className="w-8 h-8 text-[var(--accent-primary)]"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={2}
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
            </div>

            {/* 波纹效果 */}
            <div className="absolute inset-0 flex items-center justify-center">
              <div className="w-16 h-16 rounded-full border-2 border-[var(--accent-primary)] opacity-30 animate-ping-slow" />
            </div>
          </div>

          <p className="text-sm text-[var(--text-secondary)] animate-pulse">
            正在发送到 ERP...
          </p>
        </div>
      )}

      {/* 成功阶段 */}
      {phase === 'success' && (
        <div className="flex flex-col items-center py-6 animate-fade-in">
          {/* 成功图标 */}
          <div className="relative w-16 h-16 mb-4">
            {/* 绿色圆圈 */}
            <div className="absolute inset-0 rounded-full bg-green-100 animate-scale-in" />
            {/* 勾选动画 */}
            <svg
              className="absolute inset-0 w-16 h-16 text-green-500"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                className="animate-check-draw"
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2.5}
                d="M5 13l4 4L19 7"
                strokeDasharray="24"
                strokeDashoffset="24"
              />
            </svg>
          </div>

          <p className="text-base font-medium text-green-600 mb-3">
            发送成功
          </p>

          {/* 统计信息 */}
          <div className="w-full max-w-xs space-y-2 text-sm">
            <div className="flex justify-between text-[var(--text-secondary)]">
              <span>日期范围</span>
              <span className="font-medium text-[var(--text-primary)]">{card.dateRange}</span>
            </div>
            <div className="flex justify-between text-[var(--text-secondary)]">
              <span>订单数量</span>
              <span className="font-medium text-[var(--text-primary)]">{card.orderCount} 个</span>
            </div>
            <div className="flex justify-between text-[var(--text-secondary)]">
              <span>总数量</span>
              <span className="font-medium text-[var(--text-primary)]">{card.totalQuantity.toLocaleString()} 件</span>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
