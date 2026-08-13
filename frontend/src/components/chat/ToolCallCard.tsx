'use client';

interface ToolCall {
  name: string;
  input: Record<string, unknown>;
  output?: string;
}

interface ProgressCardProps {
  toolCalls: ToolCall[];
  isStreaming?: boolean;
}

// 工具名称映射为友好的中文描述
const TOOL_LABELS: Record<string, string> = {
  query_orders: '查询订单信息',
  query_orders_by_customer: '按客户查询订单',
  query_container: '查询货柜状态',
  query_containers_by_customer: '按客户查询货柜',
  reschedule: '重排订单',
  compare_schedules: '对比排产方案',
  get_schedule_kpi: '获取 KPI 指标',
};

function getToolLabel(name: string): string {
  return TOOL_LABELS[name] || name;
}

export function ProgressCard({ toolCalls, isStreaming = false }: ProgressCardProps) {
  if (!toolCalls || toolCalls.length === 0) {
    return null;
  }

  // 如果所有工具都执行完成且不再流式传输，隐藏 Progress 卡片
  const allCompleted = toolCalls.every(t => t.output !== undefined);
  if (allCompleted && !isStreaming) {
    return null;
  }

  // 判断每个步骤的状态
  const getStepStatus = (tool: ToolCall, index: number): 'completed' | 'running' | 'pending' => {
    if (tool.output !== undefined) {
      return 'completed';
    }
    // 如果前面的步骤都完成了，这个就是正在执行的
    const prevAllCompleted = toolCalls.slice(0, index).every(t => t.output !== undefined);
    if (prevAllCompleted) {
      return 'running';
    }
    return 'pending';
  };

  return (
    <div>
      {/* Steps List */}
      <div className="px-4 py-3 space-y-3">
        {toolCalls.map((tool, idx) => {
          const status = getStepStatus(tool, idx);
          const stepNumber = idx + 1;

          return (
            <div key={idx} className="flex items-center gap-3">
              {/* Step Number Circle */}
              <div className="relative flex-shrink-0">
                {status === 'running' ? (
                  // 执行中 - 蓝色圆环动画
                  <div className="w-6 h-6 relative">
                    {/* 旋转的圆弧 */}
                    <div className="absolute inset-0 animate-spin">
                      <svg className="w-6 h-6" viewBox="0 0 24 24">
                        <circle
                          cx="12"
                          cy="12"
                          r="10"
                          fill="none"
                          stroke="#E5E7EB"
                          strokeWidth="2"
                        />
                        <circle
                          cx="12"
                          cy="12"
                          r="10"
                          fill="none"
                          stroke="#3B82F6"
                          strokeWidth="2"
                          strokeLinecap="round"
                          strokeDasharray="16 47"
                        />
                      </svg>
                    </div>
                    <span className="absolute inset-0 flex items-center justify-center text-xs font-medium text-[#3B82F6]">
                      {stepNumber}
                    </span>
                  </div>
                ) : (
                  // 已完成或待执行 - 灰色圆圈
                  <div
                    className={`w-6 h-6 rounded-full border-2 flex items-center justify-center ${
                      status === 'completed'
                        ? 'border-[#D1D5DB] text-[#6B7280]'
                        : 'border-[#E5E7EB] text-[#9CA3AF]'
                    }`}
                  >
                    <span className="text-xs font-medium">{stepNumber}</span>
                  </div>
                )}
              </div>

              {/* Step Label */}
              <span
                className={`text-sm ${
                  status === 'pending'
                    ? 'text-[#9CA3AF]'
                    : 'text-[var(--text-primary)] font-medium'
                }`}
              >
                {getToolLabel(tool.name)}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}

// 保留旧的导出以兼容
interface ToolCallCardProps {
  name: string;
  input: Record<string, unknown>;
  output?: string;
}

export function ToolCallCard({ name, input, output }: ToolCallCardProps) {
  return <ProgressCard toolCalls={[{ name, input, output }]} isStreaming={output === undefined} />;
}
