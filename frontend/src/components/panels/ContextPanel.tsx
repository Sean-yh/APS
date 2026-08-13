'use client';

interface ContextItem {
  id: string;
  name: string;
  icon: string;
}

const defaultContextItems: ContextItem[] = [
  { id: 'orders', name: 'orders_erp.json', icon: '📦' },
  { id: 'inventory', name: 'inventory_erp.json', icon: '🏭' },
];

export function ContextPanel() {
  return (
    <div className="space-y-1">
      {defaultContextItems.map((item) => (
        <div
          key={item.id}
          className="flex items-center gap-2 px-2 py-1.5 rounded hover:bg-[var(--bg-hover)] transition-colors"
        >
          <span>{item.icon}</span>
          <span className="text-sm text-[var(--text-primary)]">{item.name}</span>
        </div>
      ))}
    </div>
  );
}
