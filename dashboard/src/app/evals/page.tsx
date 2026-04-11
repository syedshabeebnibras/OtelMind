"use client";

import { useMemo, useState } from "react";
import {
  CheckCircle2,
  XCircle,
  Clock,
  FlaskConical,
  TrendingUp,
  TrendingDown,
  RefreshCw,
} from "lucide-react";

import { useEvalRuns } from "@/hooks/use-traces";
import type { EvalRun } from "@/lib/api";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDate, formatPercent, truncate } from "@/lib/utils";

/** Pick the "primary" score (highest weighted avg) to headline each run row. */
function primaryScore(scores: Record<string, number> | null): number | null {
  if (!scores) return null;
  const values = Object.values(scores);
  if (values.length === 0) return null;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

function statusBadge(status: string, passed: boolean | null) {
  if (status === "pending" || status === "running") {
    return (
      <Badge variant="outline" className="gap-1">
        <Clock className="h-3 w-3" />
        {status}
      </Badge>
    );
  }
  if (passed === true) {
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" />
        passed
      </Badge>
    );
  }
  if (passed === false) {
    return (
      <Badge variant="destructive" className="gap-1">
        <XCircle className="h-3 w-3" />
        failed
      </Badge>
    );
  }
  return <Badge variant="secondary">{status}</Badge>;
}

export default function EvalsPage() {
  const { runs, total, isLoading, isError, mutate } = useEvalRuns(50);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const selected = useMemo(
    () => runs.find((r) => r.id === selectedId) ?? null,
    [runs, selectedId],
  );

  // Aggregate KPIs computed client-side from the 50 most-recent runs.
  const stats = useMemo(() => {
    const completed = runs.filter((r) => r.passed !== null);
    const passed = completed.filter((r) => r.passed === true).length;
    const failed = completed.filter((r) => r.passed === false).length;
    const passRate = completed.length > 0 ? (passed / completed.length) * 100 : 0;
    const totalRegressions = runs.reduce((a, r) => a + r.regression_count, 0);
    const totalImprovements = runs.reduce((a, r) => a + r.improvement_count, 0);
    return { passed, failed, passRate, totalRegressions, totalImprovements };
  }, [runs]);

  return (
    <div className="p-6 space-y-5">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Evaluation runs</h1>
          <p className="mt-0.5 text-sm text-slate-400">
            Regression-testing history — scored by LLM judge across dimensions
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
          label="Total runs"
          value={isLoading ? null : total.toLocaleString()}
          icon={<FlaskConical className="h-4 w-4" />}
          tint="text-blue-400"
        />
        <KpiCard
          label="Pass rate"
          value={isLoading ? null : formatPercent(stats.passRate)}
          icon={<CheckCircle2 className="h-4 w-4" />}
          tint="text-emerald-400"
        />
        <KpiCard
          label="Regressions"
          value={isLoading ? null : String(stats.totalRegressions)}
          icon={<TrendingDown className="h-4 w-4" />}
          tint="text-red-400"
        />
        <KpiCard
          label="Improvements"
          value={isLoading ? null : String(stats.totalImprovements)}
          icon={<TrendingUp className="h-4 w-4" />}
          tint="text-emerald-400"
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-4">
        {/* Runs table */}
        <div className="lg:col-span-3 rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
          {isError ? (
            <div className="flex flex-col items-center justify-center py-16 text-slate-500">
              <p className="text-sm font-medium">Failed to load eval runs</p>
              <Button variant="outline" size="sm" className="mt-4" onClick={() => mutate()}>
                Retry
              </Button>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Score</TableHead>
                  <TableHead className="text-right">Cases</TableHead>
                  <TableHead>When</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {isLoading
                  ? Array.from({ length: 6 }).map((_, i) => (
                      <TableRow key={i}>
                        <TableCell>
                          <Skeleton className="h-4 w-40" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-5 w-16 rounded-full" />
                        </TableCell>
                        <TableCell className="text-right">
                          <Skeleton className="h-4 w-10 ml-auto" />
                        </TableCell>
                        <TableCell className="text-right">
                          <Skeleton className="h-4 w-10 ml-auto" />
                        </TableCell>
                        <TableCell>
                          <Skeleton className="h-4 w-20" />
                        </TableCell>
                      </TableRow>
                    ))
                  : runs.length === 0
                    ? (
                        <TableRow>
                          <TableCell
                            colSpan={5}
                            className="h-32 text-center text-slate-500 text-sm"
                          >
                            <FlaskConical className="mx-auto mb-2 h-6 w-6" />
                            No eval runs yet — trigger one from the CLI or CI
                          </TableCell>
                        </TableRow>
                      )
                    : runs.map((run) => {
                        const score = primaryScore(run.scores);
                        const isSelected = run.id === selectedId;
                        return (
                          <TableRow
                            key={run.id}
                            onClick={() => setSelectedId(run.id)}
                            className={`cursor-pointer ${
                              isSelected ? "bg-slate-800/60" : ""
                            }`}
                          >
                            <TableCell>
                              <div className="text-sm font-medium text-slate-200">
                                {truncate(run.name, 40)}
                              </div>
                              {run.baseline && run.candidate && (
                                <div className="mt-0.5 font-mono text-[10px] text-slate-500">
                                  {run.baseline} → {run.candidate}
                                </div>
                              )}
                            </TableCell>
                            <TableCell>{statusBadge(run.status, run.passed)}</TableCell>
                            <TableCell className="text-right font-mono text-xs text-slate-300">
                              {score === null ? "—" : `${(score * 100).toFixed(1)}%`}
                            </TableCell>
                            <TableCell className="text-right font-mono text-xs text-slate-400">
                              {run.case_count}
                            </TableCell>
                            <TableCell className="text-xs text-slate-400">
                              {formatDate(run.created_at)}
                            </TableCell>
                          </TableRow>
                        );
                      })}
              </TableBody>
            </Table>
          )}
        </div>

        {/* Detail panel */}
        <div className="lg:col-span-2">
          {selected ? (
            <EvalDetail run={selected} />
          ) : (
            <Card>
              <CardContent className="flex flex-col items-center justify-center py-16 text-center">
                <FlaskConical className="h-8 w-8 text-slate-600" />
                <p className="mt-3 text-sm text-slate-400">
                  Select a run to see its dimensional scores
                </p>
              </CardContent>
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Detail panel ─────────────────────────────────────────────────────

function EvalDetail({ run }: { run: EvalRun }) {
  const scores = run.scores ?? {};
  const entries = Object.entries(scores).sort((a, b) => b[1] - a[1]);

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <CardTitle className="text-base">{run.name}</CardTitle>
          {statusBadge(run.status, run.passed)}
        </div>
        <CardDescription>
          {run.dataset ?? "No dataset attached"}
          {run.baseline && run.candidate && (
            <span className="block mt-1 font-mono text-xs">
              {run.baseline} → {run.candidate}
            </span>
          )}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Dimensional scores */}
        <div className="space-y-2">
          {entries.length === 0 ? (
            <p className="text-xs text-slate-500">No scores recorded</p>
          ) : (
            entries.map(([dim, value]) => (
              <div key={dim}>
                <div className="flex items-center justify-between text-xs">
                  <span className="text-slate-400 capitalize">{dim.replace(/_/g, " ")}</span>
                  <span className="font-mono text-slate-300">
                    {(value * 100).toFixed(1)}%
                  </span>
                </div>
                <div className="mt-1 h-1.5 rounded-full bg-slate-800 overflow-hidden">
                  <div
                    className={`h-full rounded-full ${
                      value > 0.9
                        ? "bg-emerald-500"
                        : value > 0.7
                          ? "bg-blue-500"
                          : value > 0.5
                            ? "bg-amber-500"
                            : "bg-red-500"
                    }`}
                    style={{ width: `${value * 100}%` }}
                  />
                </div>
              </div>
            ))
          )}
        </div>

        {/* Deltas */}
        <div className="grid grid-cols-3 gap-2 pt-2 border-t border-slate-800">
          <MiniStat label="Cases" value={String(run.case_count)} />
          <MiniStat
            label="Regressions"
            value={String(run.regression_count)}
            tint={run.regression_count > 0 ? "text-red-400" : "text-slate-300"}
          />
          <MiniStat
            label="Improvements"
            value={String(run.improvement_count)}
            tint={run.improvement_count > 0 ? "text-emerald-400" : "text-slate-300"}
          />
        </div>
      </CardContent>
    </Card>
  );
}

function MiniStat({
  label,
  value,
  tint = "text-slate-300",
}: {
  label: string;
  value: string;
  tint?: string;
}) {
  return (
    <div className="text-center">
      <p className="text-[10px] uppercase tracking-wider text-slate-500">{label}</p>
      <p className={`mt-1 text-lg font-semibold ${tint}`}>{value}</p>
    </div>
  );
}

// ── KPI card (shared) ────────────────────────────────────────────────

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
