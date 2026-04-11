"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  Activity,
  AlertTriangle,
  DollarSign,
  Bell,
  FlaskConical,
  Zap,
} from "lucide-react";
import { cn } from "@/lib/utils";

const navItems = [
  {
    label: "Traces",
    href: "/traces",
    icon: Activity,
    description: "Trace explorer",
  },
  {
    label: "Failures",
    href: "/failures",
    icon: AlertTriangle,
    description: "Failure analysis",
  },
  {
    label: "Costs",
    href: "/costs",
    icon: DollarSign,
    description: "Cost analytics",
  },
  {
    label: "Alerts",
    href: "/alerts",
    icon: Bell,
    description: "Alert rules",
  },
  {
    label: "Evals",
    href: "/evals",
    icon: FlaskConical,
    description: "Evaluation runs",
  },
];

export function Sidebar() {
  const pathname = usePathname();

  return (
    <aside className="fixed inset-y-0 left-0 z-40 flex w-60 flex-col border-r border-slate-800 bg-slate-950">
      {/* Logo */}
      <div className="flex h-14 items-center gap-2.5 border-b border-slate-800 px-5">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-blue-600">
          <Zap className="h-4 w-4 text-white" />
        </div>
        <div>
          <span className="text-sm font-bold text-slate-100 tracking-tight">
            OtelMind
          </span>
          <span className="ml-1.5 rounded-full bg-blue-500/20 px-1.5 py-0.5 text-[10px] font-medium text-blue-400 border border-blue-500/30">
            beta
          </span>
        </div>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto py-4 px-3">
        <p className="mb-2 px-2 text-[10px] font-semibold uppercase tracking-widest text-slate-500">
          Observability
        </p>
        <ul className="space-y-0.5">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive =
              pathname === item.href || pathname.startsWith(item.href + "/");

            return (
              <li key={item.href}>
                <Link
                  href={item.href}
                  className={cn(
                    "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-all",
                    isActive
                      ? "bg-blue-600/15 text-blue-400 border border-blue-500/20"
                      : "text-slate-400 hover:bg-slate-800/60 hover:text-slate-200 border border-transparent"
                  )}
                >
                  <Icon
                    className={cn(
                      "h-4 w-4 shrink-0 transition-colors",
                      isActive
                        ? "text-blue-400"
                        : "text-slate-500 group-hover:text-slate-300"
                    )}
                  />
                  {item.label}
                </Link>
              </li>
            );
          })}
        </ul>
      </nav>

      {/* Footer */}
      <div className="border-t border-slate-800 p-4">
        <div className="rounded-lg bg-slate-900 px-3 py-2.5">
          <p className="text-xs font-medium text-slate-400">API Endpoint</p>
          <p className="mt-0.5 truncate text-xs text-slate-500 font-mono">
            {process.env.NEXT_PUBLIC_API_URL || "localhost:8000"}
          </p>
        </div>
      </div>
    </aside>
  );
}
