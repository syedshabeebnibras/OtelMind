"use client";

import { useMemo, useState } from "react";
import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { DollarSign, RefreshCw, TrendingUp } from "lucide-react";

import { useCostBreakdown } from "@/hooks/use-traces";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatCost, formatDateShort, formatTokens } from "@/lib/utils";

/** Projects monthly spend from the observed period's daily average. */
function projectMonthlySpend(
  dailySpend: { date: string; cost: number }[],
  totalCost: number,
): number {
  const days = dailySpend.length || 1;
  return (totalCost / days) * 30;
}

/** Finds the single largest daily spend — surfaced as a "peak day" stat. */
function findPeakDay(
  dailySpend: { date: string; cost: number }[],
): { date: string; cost: number } | null {
  if (dailySpend.length === 0) return null;
  return dailySpend.reduce((max, d) => (d.cost > max.cost ? d : max));
}

function todayIso(): string {
  return new Date().toISOString().slice(0, 10);
}

function daysAgoIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return d.toISOString().slice(0, 10);
}

export default function CostsPage() {
  const [startDate, setStartDate] = useState(daysAgoIso(29));
  const [endDate, setEndDate] = useState(todayIso());

  const { breakdown, totalCost, dailySpend, isLoading, isError } =
    useCostBreakdown({ start_date: startDate, end_date: endDate });

  const projectedMonthly = useMemo(
    () => projectMonthlySpend(dailySpend, totalCost),
    [dailySpend, totalCost],
  );
  const peakDay = useMemo(() => findPeakDay(dailySpend), [dailySpend]);

  // Normalize daily series for Recharts — keep the original date strings
  // but add a short display label so the x-axis doesn't wrap.
  const chartData = useMemo(
    () =>
      dailySpend.map((d) => ({
        ...d,
        label: d.date.slice(5), // MM-DD
      })),
    [dailySpend],
  );

  const topModels = useMemo(
    () => [...breakdown].sort((a, b) => b.estimated_cost - a.estimated_cost).slice(0, 8),
    [breakdown],
  );

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Cost Analytics</h1>
          <p className="mt-0.5 text-sm text-slate-400">
            Model spend across your traces — updated in near real-time
          </p>
        </div>
        <div className="flex items-center gap-3">
          <Input
            type="date"
            value={startDate}
            onChange={(e) => setStartDate(e.target.value)}
            className="w-40 text-slate-400"
          />
          <span className="text-slate-500 text-xs">to</span>
          <Input
            type="date"
            value={endDate}
            onChange={(e) => setEndDate(e.target.value)}
            className="w-40 text-slate-400"
          />
        </div>
      </div>

      {/* KPI cards */}
      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <KpiCard
          label="Spend in period"
          value={isLoading ? null : formatCost(totalCost)}
          icon={<DollarSign className="h-4 w-4" />}
          tint="text-emerald-400"
        />
        <KpiCard
          label="Projected monthly"
          value={isLoading ? null : formatCost(projectedMonthly)}
          icon={<TrendingUp className="h-4 w-4" />}
          tint="text-blue-400"
        />
        <KpiCard
          label="Peak day"
          value={
            isLoading
              ? null
              : peakDay
                ? formatCost(peakDay.cost)
                : "—"
          }
          sub={peakDay ? formatDateShort(peakDay.date + "T00:00:00Z") : undefined}
          icon={<TrendingUp className="h-4 w-4" />}
          tint="text-amber-400"
        />
        <KpiCard
          label="Models tracked"
          value={isLoading ? null : String(breakdown.length)}
          icon={<RefreshCw className="h-4 w-4" />}
          tint="text-violet-400"
        />
      </div>

      {/* Daily spend chart */}
      <Card>
        <CardHeader>
          <CardTitle>Daily spend</CardTitle>
          <CardDescription>
            Rolling window of USD spend per day across every model
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isLoading ? (
            <Skeleton className="h-64 w-full" />
          ) : chartData.length === 0 ? (
            <div className="flex h-64 items-center justify-center text-sm text-slate-500">
              No spend recorded in this range
            </div>
          ) : (
            <div className="h-64 w-full">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart data={chartData} margin={{ top: 5, right: 8, bottom: 5, left: 0 }}>
                  <defs>
                    <linearGradient id="costGradient" x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor="#3b82f6" stopOpacity={0.5} />
                      <stop offset="100%" stopColor="#3b82f6" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis dataKey="label" stroke="#64748b" fontSize={11} tickLine={false} />
                  <YAxis
                    stroke="#64748b"
                    fontSize={11}
                    tickLine={false}
                    tickFormatter={(v) => formatCost(Number(v))}
                    width={56}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#0f172a",
                      border: "1px solid #1e293b",
                      borderRadius: 8,
                      color: "#f1f5f9",
                      fontSize: 12,
                    }}
                    formatter={(v: unknown) => [formatCost(Number(v)), "Cost"]}
                  />
                  <Area
                    type="monotone"
                    dataKey="cost"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    fill="url(#costGradient)"
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </CardContent>
      </Card>

      {/* Top models bar + table */}
      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        <Card className="lg:col-span-2">
          <CardHeader>
            <CardTitle>Top models by spend</CardTitle>
            <CardDescription>Highest-cost models in the selected period</CardDescription>
          </CardHeader>
          <CardContent>
            {isLoading ? (
              <Skeleton className="h-64 w-full" />
            ) : topModels.length === 0 ? (
              <div className="flex h-64 items-center justify-center text-sm text-slate-500">
                No model data
              </div>
            ) : (
              <div className="h-64 w-full">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart
                    data={topModels}
                    layout="vertical"
                    margin={{ top: 5, right: 12, bottom: 5, left: 12 }}
                  >
                    <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" horizontal={false} />
                    <XAxis
                      type="number"
                      stroke="#64748b"
                      fontSize={11}
                      tickFormatter={(v) => formatCost(Number(v))}
                    />
                    <YAxis
                      type="category"
                      dataKey="model"
                      stroke="#64748b"
                      fontSize={10}
                      width={110}
                      tickLine={false}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: "#0f172a",
                        border: "1px solid #1e293b",
                        borderRadius: 8,
                        fontSize: 12,
                      }}
                      formatter={(v: unknown) => [formatCost(Number(v)), "Spend"]}
                    />
                    <Bar dataKey="estimated_cost" fill="#3b82f6" radius={[0, 4, 4, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </CardContent>
        </Card>

        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>Model breakdown</CardTitle>
            <CardDescription>
              Token usage and cost attribution per model
            </CardDescription>
          </CardHeader>
          <CardContent className="px-0">
            {isError ? (
              <div className="flex h-48 items-center justify-center text-sm text-slate-500">
                Failed to load cost breakdown
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Model</TableHead>
                    <TableHead className="text-right">Prompt</TableHead>
                    <TableHead className="text-right">Completion</TableHead>
                    <TableHead className="text-right">Total</TableHead>
                    <TableHead className="text-right">Spend</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {isLoading
                    ? Array.from({ length: 5 }).map((_, i) => (
                        <TableRow key={i}>
                          <TableCell>
                            <Skeleton className="h-4 w-24" />
                          </TableCell>
                          <TableCell className="text-right">
                            <Skeleton className="h-4 w-12 ml-auto" />
                          </TableCell>
                          <TableCell className="text-right">
                            <Skeleton className="h-4 w-12 ml-auto" />
                          </TableCell>
                          <TableCell className="text-right">
                            <Skeleton className="h-4 w-12 ml-auto" />
                          </TableCell>
                          <TableCell className="text-right">
                            <Skeleton className="h-4 w-14 ml-auto" />
                          </TableCell>
                        </TableRow>
                      ))
                    : breakdown.length === 0
                      ? (
                          <TableRow>
                            <TableCell
                              colSpan={5}
                              className="h-32 text-center text-slate-500 text-sm"
                            >
                              No cost data for this period
                            </TableCell>
                          </TableRow>
                        )
                      : breakdown.map((row) => (
                          <TableRow key={row.model}>
                            <TableCell className="font-mono text-xs text-slate-300">
                              {row.model}
                            </TableCell>
                            <TableCell className="text-right text-xs text-slate-400 font-mono">
                              {formatTokens(row.prompt_tokens)}
                            </TableCell>
                            <TableCell className="text-right text-xs text-slate-400 font-mono">
                              {formatTokens(row.completion_tokens)}
                            </TableCell>
                            <TableCell className="text-right text-xs text-slate-300 font-mono">
                              {formatTokens(row.total_tokens)}
                            </TableCell>
                            <TableCell className="text-right text-sm font-medium text-emerald-400">
                              {formatCost(row.estimated_cost)}
                            </TableCell>
                          </TableRow>
                        ))}
                </TableBody>
              </Table>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

// ── Helpers ──────────────────────────────────────────────────────────

interface KpiCardProps {
  label: string;
  value: string | null;
  sub?: string;
  icon?: React.ReactNode;
  tint?: string;
}

function KpiCard({ label, value, sub, icon, tint = "text-slate-400" }: KpiCardProps) {
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
          {sub && <p className="mt-1 text-xs text-slate-500">{sub}</p>}
        </div>
      </CardContent>
    </Card>
  );
}
