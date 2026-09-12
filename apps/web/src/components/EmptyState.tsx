import type { ReactNode } from "react";

export interface EmptyStateProps {
  title: string;
  description: string;
  action?: ReactNode;
  icon?: ReactNode;
  className?: string;
}

export function EmptyState({ title, description, action, icon, className = "" }: EmptyStateProps) {
  return (
    <div
      className={`surface flex flex-col items-center justify-center gap-3 p-8 text-center animate-fade-up ${className}`.trim()}
    >
      {icon && (
        <div className="text-2xl text-ink-400 select-none" aria-hidden="true">
          {icon}
        </div>
      )}
      <h3 className="panel-title">{title}</h3>
      <p className="max-w-md text-sm text-ink-400">{description}</p>
      {action && <div className="mt-1 flex items-center justify-center gap-2">{action}</div>}
    </div>
  );
}
