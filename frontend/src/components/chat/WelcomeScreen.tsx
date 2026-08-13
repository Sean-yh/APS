'use client';

interface WelcomeScreenProps {
  onSuggestionClick?: (suggestion: string) => void;
}

const SUGGESTIONS = [
  '更新排产',
  '最新排产准时率',
  '目前哪些客人的PO会延期',
];

export function WelcomeScreen({ onSuggestionClick }: WelcomeScreenProps) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center p-8">
      <div className="max-w-2xl w-full text-center">
        {/* 标题 */}
        <h1 className="text-2xl font-semibold text-[var(--text-primary)] mb-8">
          金福华排产
        </h1>

        {/* 建议提示 */}
        <div className="grid grid-cols-1 gap-3 max-w-md mx-auto">
          {SUGGESTIONS.map((suggestion, index) => (
            <button
              key={index}
              onClick={() => onSuggestionClick?.(suggestion)}
              className="
                px-4 py-3 rounded-xl
                bg-[var(--bg-tertiary)] hover:bg-[var(--bg-hover)]
                border border-[var(--border-primary)]
                text-left text-sm text-[var(--text-secondary)]
                hover:text-[var(--text-primary)]
                transition-colors duration-150
              "
            >
              <span className="text-[var(--accent-primary)] mr-2">→</span>
              {suggestion}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
