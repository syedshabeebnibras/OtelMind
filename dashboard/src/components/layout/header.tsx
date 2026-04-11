"use client";

import { usePathname } from "next/navigation";
import { ChevronRight } from "lucide-react";
import { cn } from "@/lib/utils";

const routeMeta: Record<string, { label: string; description?: string }> = {
  "/traces": { label: "Traces", description: "Explore all agent traces" },
  "/failures": { label: "Failures", description: "Failure detection and analysis" },
  "/costs": { label: "Costs", description: "Token usage and cost analytics" },
  "/alerts": { label: "Alerts", description: "Alert rules and notifications" },
};

function ServiceStatusDot({ healthy = true }: { healthy?: boolean }) {
  return (
    <div className="flex items-center gap-2">
      <div className="relative flex h-2.5 w-2.5">
        <span
          className={cn(
            "absolute inline-flex h-full w-full animate-ping rounded-full opacity-75",
            healthy ? "bg-emerald-400" : "bg-red-400"
          )}
        />
        <span
          className={cn(
            "relative inline-flex h-2.5 w-2.5 rounded-full",
            healthy ? "bg-emerald-500" : "bg-red-500"
          )}
        />
      </div>
      <span className="text-xs text-slate-400">
        {healthy ? "API Connected" : "API Unreachable"}
      </span>
    </div>
  );
}

export function Header() {
  const pathname = usePathname();

  // Find the best matching route
  const routeKey = Object.keys(routeMeta)
    .filter((k) => pathname === k || pathname.startsWith(k + "/"))
    .sort((a, b) => b.length - a.length)[0];

  const meta = routeKey ? routeMeta[routeKey] : null;

  // Build breadcrumb segments
  const segments = pathname.split("/").filter(Boolean);

  return (
    <header className="sticky top-0 z-30 flex h-14 items-center justify-between border-b border-slate-800 bg-slate-950/90 backdrop-blur-sm px-6">
      {/* Breadcrumb */}
      <nav className="flex items-center gap-1.5 text-sm">
        <span className="text-slate-500">OtelMind</span>
        {segments.map((segment, i) => {
          const isLast = i === segments.length - 1;
          const label =
            i === 0
              ? (routeMeta[`/${segment}`]?.label ?? segment)
              : segment.length > 16
              ? `${segment.slice(0, 8)}…${segment.slice(-6)}`
              : segment;

          return (
            <span key={i} className="flex items-center gap-1.5">
              <ChevronRight className="h-3.5 w-3.5 text-slate-600" />
              <span
                className={cn(
                  "font-medium",
                  isLast ? "text-slate-100" : "text-slate-400"
                )}
              >
                {label}
              </span>
            </span>
          );
        })}
      </nav>

      {/* Right side: description + status */}
      <div className="flex items-center gap-5">
        {meta?.description && (
          <span className="hidden text-xs text-slate-500 md:block">
            {meta.description}
          </span>
        )}
        <ServiceStatusDot healthy />
      </div>
    </header>
  );
}
