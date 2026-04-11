"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  AlertTriangle,
  RefreshCw,
  ShieldAlert,
  Activity,
  ChevronLeft,
  ChevronRight,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";

import { useDashboardStats, useFailures } from "@/hooks/use-traces";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatPercent, formatRelativeTime, truncate } from "@/lib/utils";

const FAILURE_TYPE_OPTIONS = [
  { value: "all", label: "All failure types" },
  { value: "hallucination", label: "Hallucination" },
  { value: "tool_timeout", label: "Tool timeout" },
  { value: "infinite_loop", label: "Infinite loop" },
  { value: "context_overflow", label: "Context overflow" },
  { value: "semantic_drift", label: "Semantic drift" },
  { value: "prompt_injection", label: "Prompt injection" },
  { value: "cost_spike", label: "Cost spike" },
];

// Paired with the pie chart and the badge variants.
const FAILURE_COLORS: Record<string, string> = {
  hallucination: "#f97316",
  tool_timeout: "#eab308",
  infinite_loop: "#ef4444",
  context_overflow: "#8b5cf6",
  semantic_drift: "#06b6d4",
  prompt_injection: "#dc2626",
  cost_spike: "#22c55e",
};

function hashedColor(key: string): string {
  // Stable fallback color for failure types not in the curated palette.
  const palette = ["#3b82f6", "#f59e0b", "#10b981", "#a855f7", "#ec4899"];
  let hash = 0;
  for (const ch of key) hash = (hash * 31 + ch.charCodeAt(0)) | 0;
  return palette[Math.abs(hash) % palette.length];
}

/** Groups failure rows into 24 hourly buckets for the timeline chart. */
function bucketHourly(
  items: { timestamp: string }[],
): { hour: string; count: number }[] {
  const buckets = new Map<string, number>();
  const now = new Date();
  // Seed the last 24 hours so the chart is dense even at low volume.
  for (let i = 23; i >= 0; i--) {
    const d = new Date(now.getTime() - i * 60 * 60 * 1000);
    const key = `${d.getUTCHours().toString().padStart(2, "0")}:00`;
    buckets.set(key, 0);
  }
  for (const row of items) {
    const ts = new Date(row.timestamp);
    // Only count events within the last 24h
    const ageHours = (now.getTime() - ts.getTime()) / 3.6e6;
    if (ageHours > 24) continue;
    const key = `${ts.getUTCHours().toString().padStart(2, "0")}:00`;
    buckets.set(key, (buckets.get(key) ?? 0) + 1);
  }
  return Array.from(buckets, ([hour, count]) => ({ hour, count }));
}

export default function FailuresPage() {
  const [failureType, setFailureType] = useState("all");
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [cursorStack, setCursorStack] = useState<string[]>([]);

  const { stats, isLoading: statsLoading } = useDashboardStats();
  const { failures, total, nextCursor, isLoading, isError, mutate } = useFailures({
    cursor,
    failure_type: failureType !== "all" ? failureType : undefined,
  });

  const hourlyData = useMemo(() => bucketHourly(failures), [failures]);

  const typeBreakdown = useMemo(() => {
    const entries = Object.entries(stats?.failures_by_type ?? {});
    return entries
      .map(([type, count]) => ({
        type,
        count,
        color: FAILURE_COLORS[type] ?? hashedColor(type),
      }))
      .sort((a, b) => b.count - a.count);
  }, [stats]);

  const handleNext = () => {
    if (!nextCursor) return;
    setCursorStack((p) => [...p, cursor ?? ""]);
    setCursor(nextCursor);
  };

  const handlePrev = () => {
    const stack = [...cursorStack];
    const prev = stack.pop();
    setCursorStack(stack);
    setCursor(prev || undefined);
  };

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Failures</h1>
          <p className="mt-0.5 text-sm text-slate-400">
            Watchdog-detected incidents classified by type and confidence
          </p>
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={() => mutate()}
          disabled={isLoading}
          className="gap-2"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isLoading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </div>

      {/* KPIs */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <KpiCard
          label="Total failures"
          value={statsLoading ? null : (stats?.total_failures ?? 0).toLocaleString()}
          icon={<AlertTriangle className="h-4 w-4" />}
          tint="text-amber-400"
        />
        <KpiCard
          label="Failure rate"
          value={statsLoading ? null : formatPercent(stats?.failure_rate ?? 0)}
          icon={<ShieldAlert className="h-4 w-4" />}
          tint="text-red-400"
        />
        <KpiCard
          label="Distinct types"
          value={statsLoading ? null : String(Object.keys(stats?.failures_by_type ?? {}).length)}
          icon={<Activity className="h-4 w-4" />}
          tint="text-blue-400"
        />
        <KpiCard
          label="Active services"
          value={statsLoading ? null : String(stats?.active_services ?? 0)}
          icon={<Activity className="h-4 w-4" />}
          tint="text-violet-400"
        />
      </div>

      {/* Timeline + Pie */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>Failure rate (24h)</CardTitle>
            <CardDescription>
              Hourly count of classified failures over the last day
            </CardDescription>
          </CardHeader>
          <CardContent>
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <LineChart data={hourlyData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis
                    dataKey="hour"
                    stroke="#64748b"
                    fontSize={10}
                    interval={2}
                    tickLine={false}
                  />
                  <YAxis
                    stroke="#64748b"
                    fontSize={11}
                    tickLine={false}
                    allowDecimals={false}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#0f172a",
                      border: "1px solid #1e293b",
                      borderRadius: 8,
                      fontSize: 12,
                    }}
                  />
                  <Line
                    type="monotone"
                    dataKey="count"
                    stroke="#f97316"
                    strokeWidth={2}
                    dot={false}
                  />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>

        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>By type</CardTitle>
            <CardDescription>Distribution of all recorded failures</CardDescription>
          </CardHeader>
          <CardContent>
            {typeBreakdown.length === 0 ? (
              <div className="flex h-64 items-center justify-center text-sm text-slate-500">
                {statsLoading ? "Loading…" : "No failures yet"}
              </div>
            ) : (
              <div className="h-64 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <PieChart>
                    <Pie
                      data={typeBreakdown}
                      dataKey="count"
                      nameKey="type"
                      innerRadius={48}
                      outerRadius={84}
                      paddingAngle={2}
                    >
                      {typeBreakdown.map((entry) => (
                        <Cell key={entry.type} fill={entry.color} />
                      ))}
                    </Pie>
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "#0f172a",
                        border: "1px solid #1e293b",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                    />
                  </PieChart>
                </ResponsiveContainer>
              </div>
            )}
            <div className="mt-2 flex flex-wrap gap-2">
              {typeBreakdown.slice(0, 6).map((t) => (
                <span
                  key={t.type}
                  className="inline-flex items-center gap-1.5 rounded-md border border-slate-800 bg-slate-950 px-2 py-1 text-[11px] text-slate-300"
                >
                  <span
                    className="h-2 w-2 rounded-full"
                    style={{ backgroundColor: t.color }}
                  />
                  {t.type} · {t.count}
                </span>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>

      {/* Filter */}
      <div className="flex items-center gap-3 rounded-xl border border-slate-800 bg-slate-900 p-4">
        <Select
          value={failureType}
          onValueChange={(v) => {
            setFailureType(v);
            setCursor(undefined);
            setCursorStack([]);
          }}
        >
          <SelectTrigger className="w-56">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {FAILURE_TYPE_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
        <p className="ml-auto text-xs text-slate-500">
          {isLoading ? "Loading…" : `${total.toLocaleString()} total failures`}
        </p>
      </div>

      {/* Table */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
        {isError ? (
          <div className="flex flex-col items-center justify-center py-16 text-slate-500">
            <p className="text-sm font-medium">Failed to load failures</p>
            <Button variant="outline" size="sm" className="mt-4" onClick={() => mutate()}>
              Retry
            </Button>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[260px]">Trace ID</TableHead>
                <TableHead>Failure type</TableHead>
                <TableHead>Detection</TableHead>
                <TableHead className="text-right">Confidence</TableHead>
                <TableHead>When</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading
                ? Array.from({ length: 8 }).map((_, i) => (
                    <TableRow key={i}>
                      <TableCell>
                        <Skeleton className="h-4 w-48" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-5 w-24 rounded-full" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-4 w-20" />
                      </TableCell>
                      <TableCell className="text-right">
                        <Skeleton className="h-4 w-12 ml-auto" />
                      </TableCell>
                      <TableCell>
                        <Skeleton className="h-4 w-24" />
                      </TableCell>
                    </TableRow>
                  ))
                : failures.length === 0
                  ? (
                      <TableRow>
                        <TableCell
                          colSpan={5}
                          className="h-32 text-center text-slate-500 text-sm"
                        >
                          No failures found
                        </TableCell>
                      </TableRow>
                    )
                  : failures.map((f) => {
                      // eval_regression failures use a synthetic trace_id
                      // of the form `eval-<run_uuid>` — there's no matching
                      // row in traces, so link to the eval run view instead.
                      const isEvalFailure = f.trace_id.startsWith("eval-");
                      const linkHref = isEvalFailure
                        ? `/evals?run=${f.trace_id.slice(5)}`
                        : `/traces/${f.trace_id}`;
                      const linkLabel = isEvalFailure
                        ? `eval · ${truncate(f.trace_id.slice(5), 28)}`
                        : truncate(f.trace_id, 32);
                      return (
                        <TableRow key={f.id}>
                          <TableCell>
                            <Link
                              href={linkHref}
                              className="font-mono text-xs text-blue-400 hover:text-blue-300 hover:underline"
                            >
                              {linkLabel}
                            </Link>
                          </TableCell>
                          <TableCell>
                            <Badge variant="destructive">{f.failure_type}</Badge>
                          </TableCell>
                          <TableCell className="text-xs text-slate-400 capitalize">
                            {f.detection_method}
                          </TableCell>
                          <TableCell className="text-right font-mono text-xs text-slate-300">
                            {(f.confidence * 100).toFixed(0)}%
                          </TableCell>
                          <TableCell className="text-xs text-slate-400">
                            {formatRelativeTime(f.timestamp)}
                          </TableCell>
                        </TableRow>
                      );
                    })}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Pagination */}
      {!isError && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-slate-500">
            {failures.length > 0
              ? `Showing ${failures.length} of ${total.toLocaleString()}`
              : ""}
          </p>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={handlePrev}
              disabled={cursorStack.length === 0 || isLoading}
            >
              <ChevronLeft className="h-4 w-4" />
              Previous
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleNext}
              disabled={!nextCursor || isLoading}
            >
              Next
              <ChevronRight className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────

interface KpiCardProps {
  label: string;
  value: string | null;
  icon?: React.ReactNode;
  tint?: string;
}

function KpiCard({ label, value, icon, tint = "text-slate-400" }: KpiCardProps) {
  return (
    <Card>
      <CardContent className="p-5">
        <div className="flex items-center justify-between">
          <p className="text-xs uppercase tracking-wider text-slate-500 font-semibold">
            {label}
          </p>
          <span className={tint}>{icon}</span>
        </div>
        <div className="mt-2">
          {value === null ? (
            <Skeleton className="h-7 w-24" />
          ) : (
            <p className="text-2xl font-semibold text-slate-100">{value}</p>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
