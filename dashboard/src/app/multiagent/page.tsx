"use client";

import Link from "next/link";
import { RefreshCw, Users, Clock, CheckCircle2, XCircle } from "lucide-react";

import { useGroupRuns } from "@/hooks/use-traces";
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
import { formatDate, truncate } from "@/lib/utils";

function statusBadge(status: string) {
  if (status === "pending" || status === "in_progress" || status === "running") {
    return (
      <Badge variant="outline" className="gap-1">
        <Clock className="h-3 w-3" />
        {status}
      </Badge>
    );
  }
  if (status === "completed" || status === "converged") {
    return (
      <Badge variant="success" className="gap-1">
        <CheckCircle2 className="h-3 w-3" />
        {status}
      </Badge>
    );
  }
  if (status === "failed" || status === "deadlocked" || status === "budget_exceeded") {
    return (
      <Badge variant="destructive" className="gap-1">
        <XCircle className="h-3 w-3" />
        {status}
      </Badge>
    );
  }
  return <Badge variant="secondary">{status}</Badge>;
}

export default function MultiAgentPage() {
  const { runs, total, isLoading, isError, mutate } = useGroupRuns(50);

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Multi-agent runs</h1>
          <p className="mt-0.5 text-sm text-slate-400">
            Group conversations with multiple Claude agents — protocols, metrics, cost
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

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Users className="h-4 w-4 text-blue-400" />
            Recent runs ({total.toLocaleString()})
          </CardTitle>
          <CardDescription>
            Click a row to see messages, per-agent stats, and collaboration metrics
          </CardDescription>
        </CardHeader>
        <CardContent>
          {isError ? (
            <p className="text-sm text-red-400">Failed to load runs.</p>
          ) : isLoading ? (
            <div className="space-y-2">
              {Array.from({ length: 5 }).map((_, i) => (
                <Skeleton key={i} className="h-10 w-full" />
              ))}
            </div>
          ) : runs.length === 0 ? (
            <p className="text-sm text-slate-500 py-8 text-center">
              No multi-agent runs yet. POST /api/v1/multiagent/runs to spawn one.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Problem</TableHead>
                  <TableHead>Protocol</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Rounds</TableHead>
                  <TableHead className="text-right">Tokens</TableHead>
                  <TableHead className="text-right">Cost (USD)</TableHead>
                  <TableHead>Created</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {runs.map((r) => (
                  <TableRow key={r.id} className="hover:bg-slate-900/50">
                    <TableCell>
                      <Link
                        href={`/multiagent/${r.id}`}
                        className="text-blue-400 hover:underline"
                      >
                        {truncate(r.problem, 70)}
                      </Link>
                    </TableCell>
                    <TableCell className="text-xs text-slate-400 font-mono">
                      {r.protocol}
                    </TableCell>
                    <TableCell>{statusBadge(r.status)}</TableCell>
                    <TableCell className="text-right text-sm">{r.rounds_completed}</TableCell>
                    <TableCell className="text-right text-sm">
                      {r.total_tokens.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right text-sm">
                      ${r.total_cost_usd.toFixed(4)}
                    </TableCell>
                    <TableCell className="text-xs text-slate-500">
                      {formatDate(r.created_at)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
