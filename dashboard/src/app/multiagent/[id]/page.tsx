"use client";

import { ArrowLeft, RefreshCw } from "lucide-react";
import Link from "next/link";

import { InteractionGraph } from "@/components/InteractionGraph";
import { useGroupRun } from "@/hooks/use-traces";
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

interface AgentStats {
  messages_sent: number;
  tokens_used: number;
  corrections_made: number;
  corrections_received: number;
  contribution_ratio: number;
}

function MetricCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card>
      <CardHeader className="pb-1">
        <CardDescription className="text-xs uppercase tracking-wide">{label}</CardDescription>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold text-slate-100">{value}</p>
        {hint && <p className="mt-1 text-xs text-slate-500">{hint}</p>}
      </CardContent>
    </Card>
  );
}

export default function GroupRunDetailPage({
  params,
}: {
  params: { id: string };
}) {
  const { id } = params;
  const { run, isLoading, isError, mutate } = useGroupRun(id);

  if (isLoading) {
    return (
      <div className="p-6 space-y-4">
        <Skeleton className="h-8 w-72" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }
  if (isError || !run) {
    return (
      <div className="p-6">
        <p className="text-sm text-red-400">Run not found or failed to load.</p>
        <Link href="/multiagent" className="text-blue-400 underline text-sm mt-2 inline-block">
          ← Back to runs
        </Link>
      </div>
    );
  }

  const metrics = (run.metrics ?? {}) as Record<string, unknown>;
  const perAgent = (metrics.per_agent_stats ?? {}) as Record<string, AgentStats>;
  const messages = run.messages ?? [];

  const num = (k: string, fallback = 0): number => {
    const v = metrics[k];
    return typeof v === "number" ? v : fallback;
  };

  return (
    <div className="p-6 space-y-5">
      <div className="flex items-center justify-between">
        <div>
          <Link
            href="/multiagent"
            className="inline-flex items-center gap-1 text-xs text-slate-400 hover:text-slate-200 mb-1"
          >
            <ArrowLeft className="h-3 w-3" />
            All runs
          </Link>
          <h1 className="text-xl font-semibold text-slate-100">Group run</h1>
          <p className="mt-0.5 text-sm text-slate-400 font-mono">
            {run.id}
          </p>
        </div>
        <Button variant="outline" size="sm" onClick={() => mutate()} className="gap-2">
          <RefreshCw className="h-3.5 w-3.5" />
          Refresh
        </Button>
      </div>

      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-base">Problem</CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm text-slate-300 whitespace-pre-wrap">{run.problem}</p>
          <div className="mt-3 flex flex-wrap gap-2 text-xs">
            <Badge variant="secondary">{run.protocol}</Badge>
            <Badge variant="outline">{run.status}</Badge>
            <Badge variant="outline">{run.rounds_completed} rounds</Badge>
          </div>
        </CardContent>
      </Card>

      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        <MetricCard
          label="Convergence"
          value={num("convergence_rate").toFixed(2)}
          hint="0.0–1.0"
        />
        <MetricCard
          label="Dominance"
          value={num("dominance_score").toFixed(2)}
          hint="1.0 = balanced"
        />
        <MetricCard
          label="Comm. efficiency"
          value={num("communication_efficiency").toFixed(2)}
        />
        <MetricCard
          label="Task completion"
          value={num("task_completion_score").toFixed(2)}
        />
        <MetricCard
          label="Total cost"
          value={`$${num("total_cost_usd").toFixed(4)}`}
          hint={`${run.total_tokens.toLocaleString()} tokens`}
        />
      </div>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Per-agent stats</CardTitle>
        </CardHeader>
        <CardContent>
          {Object.keys(perAgent).length === 0 ? (
            <p className="text-sm text-slate-500">No per-agent stats yet.</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Agent</TableHead>
                  <TableHead className="text-right">Messages</TableHead>
                  <TableHead className="text-right">Tokens</TableHead>
                  <TableHead className="text-right">Corrections made</TableHead>
                  <TableHead className="text-right">Corrections received</TableHead>
                  <TableHead className="text-right">Contribution</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {Object.entries(perAgent).map(([agentId, s]) => (
                  <TableRow key={agentId}>
                    <TableCell className="font-mono text-xs">{agentId}</TableCell>
                    <TableCell className="text-right text-sm">{s.messages_sent}</TableCell>
                    <TableCell className="text-right text-sm">
                      {s.tokens_used.toLocaleString()}
                    </TableCell>
                    <TableCell className="text-right text-sm">{s.corrections_made}</TableCell>
                    <TableCell className="text-right text-sm">
                      {s.corrections_received}
                    </TableCell>
                    <TableCell className="text-right text-sm">
                      {(s.contribution_ratio * 100).toFixed(1)}%
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Interaction graph</CardTitle>
          <CardDescription>
            Sequence-diagram view: who talked to whom, in what round, and where
            corrections happened
          </CardDescription>
        </CardHeader>
        <CardContent>
          <InteractionGraph
            messages={messages as Parameters<typeof InteractionGraph>[0]["messages"]}
            perAgentStats={perAgent}
          />
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-base">Message timeline</CardTitle>
          <CardDescription>
            {messages.length} message{messages.length !== 1 ? "s" : ""} across {run.rounds_completed} round
            {run.rounds_completed !== 1 ? "s" : ""}
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          {messages.length === 0 ? (
            <p className="text-sm text-slate-500">No messages persisted yet.</p>
          ) : (
            messages.map((msg, i) => {
              const m = msg as Record<string, unknown>;
              const role = String(m.sender_role ?? "?");
              const tokens =
                ((m.token_usage as Record<string, number> | null) ?? {}).total_tokens ?? 0;
              return (
                <div
                  key={i}
                  className="rounded-lg border border-slate-800 bg-slate-900/40 p-3"
                >
                  <div className="flex items-center justify-between mb-2">
                    <div className="flex items-center gap-2">
                      <Badge variant="secondary">{role}</Badge>
                      <span className="text-xs text-slate-500">
                        round {String(m.round_number ?? 0)}
                      </span>
                    </div>
                    <span className="text-xs text-slate-500">{tokens} tok</span>
                  </div>
                  <p className="text-sm text-slate-300 whitespace-pre-wrap">
                    {String(m.content ?? "")}
                  </p>
                </div>
              );
            })
          )}
        </CardContent>
      </Card>
    </div>
  );
}
