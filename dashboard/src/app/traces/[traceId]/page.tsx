"use client";

import { useState } from "react";
import Link from "next/link";
import { ArrowLeft, Clock, Coins, ChevronRight, X, AlertCircle } from "lucide-react";
import { useTrace } from "@/hooks/use-traces";
import { Badge, statusVariant } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import type { Span } from "@/lib/api";
import {
  formatDuration,
  formatDate,
  formatCost,
  formatTokens,
  cn,
} from "@/lib/utils";

// ── Waterfall helpers ────────────────────────────────────────────────────────

function getSpanColor(status: string): string {
  switch (status?.toLowerCase()) {
    case "success":
    case "ok":
      return "bg-emerald-500";
    case "error":
    case "failed":
      return "bg-red-500";
    case "warning":
      return "bg-amber-500";
    case "running":
      return "bg-blue-500 animate-pulse";
    default:
      return "bg-slate-500";
  }
}

interface WaterfallBarProps {
  span: Span;
  traceStart: number;
  traceDuration: number;
  depth: number;
  isSelected: boolean;
  onClick: () => void;
}

function WaterfallBar({
  span,
  traceStart,
  traceDuration,
  depth,
  isSelected,
  onClick,
}: WaterfallBarProps) {
  const spanStart = new Date(span.start_time).getTime();
  const offsetPct =
    traceDuration > 0
      ? ((spanStart - traceStart) / traceDuration) * 100
      : 0;
  const widthPct =
    traceDuration > 0
      ? Math.max((span.duration_ms / traceDuration) * 100, 0.5)
      : 1;

  return (
    <button
      onClick={onClick}
      className={cn(
        "group flex w-full items-center gap-3 rounded px-3 py-1.5 text-left transition-colors",
        isSelected
          ? "bg-blue-600/15 border border-blue-500/30"
          : "hover:bg-slate-800/50 border border-transparent"
      )}
    >
      {/* Span name with indent */}
      <div
        className="flex-shrink-0 w-48 truncate text-xs"
        style={{ paddingLeft: `${depth * 12}px` }}
      >
        {depth > 0 && (
          <ChevronRight className="inline h-3 w-3 text-slate-600 mr-1" />
        )}
        <span className={cn(isSelected ? "text-blue-300" : "text-slate-300 group-hover:text-slate-100")}>
          {span.name}
        </span>
      </div>

      {/* Timeline track */}
      <div className="relative flex-1 h-5 rounded overflow-hidden bg-slate-800/50">
        <div
          className={cn("absolute top-1 h-3 rounded waterfall-bar", getSpanColor(span.status))}
          style={{
            left: `${Math.min(offsetPct, 99.5)}%`,
            width: `${Math.min(widthPct, 100 - offsetPct)}%`,
          }}
          title={`${span.name}: ${formatDuration(span.duration_ms)}`}
        />
      </div>

      {/* Duration label */}
      <div className="flex-shrink-0 w-16 text-right font-mono text-xs text-slate-500">
        {formatDuration(span.duration_ms)}
      </div>
    </button>
  );
}

// ── Span inspector ────────────────────────────────────────────────────────────

function SpanInspector({
  span,
  onClose,
}: {
  span: Span;
  onClose: () => void;
}) {
  return (
    <div className="flex flex-col h-full overflow-hidden rounded-xl border border-slate-700 bg-slate-900">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium text-slate-100 truncate">{span.name}</p>
          <p className="text-xs text-slate-500 font-mono truncate">{span.span_id}</p>
        </div>
        <button
          onClick={onClose}
          className="ml-2 rounded-md p-1 text-slate-500 hover:text-slate-300 hover:bg-slate-800 transition-colors"
        >
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 space-y-4">
        {/* Status & timing */}
        <div className="flex items-center gap-2 flex-wrap">
          <Badge variant={statusVariant(span.status)}>{span.status}</Badge>
          <span className="text-xs text-slate-400 font-mono">
            {formatDuration(span.duration_ms)}
          </span>
        </div>

        <div className="grid grid-cols-2 gap-2 text-xs">
          <div>
            <p className="text-slate-500">Start</p>
            <p className="text-slate-300 font-mono">{formatDate(span.start_time)}</p>
          </div>
          <div>
            <p className="text-slate-500">End</p>
            <p className="text-slate-300 font-mono">{formatDate(span.end_time)}</p>
          </div>
        </div>

        {/* Token usage */}
        {(span.prompt_tokens != null || span.completion_tokens != null) && (
          <div className="rounded-lg bg-slate-800/60 p-3 space-y-1.5">
            <p className="text-xs font-medium text-slate-400">Token Usage</p>
            <div className="grid grid-cols-3 gap-2 text-xs">
              <div>
                <p className="text-slate-500">Prompt</p>
                <p className="font-mono text-slate-200">
                  {formatTokens(span.prompt_tokens ?? 0)}
                </p>
              </div>
              <div>
                <p className="text-slate-500">Completion</p>
                <p className="font-mono text-slate-200">
                  {formatTokens(span.completion_tokens ?? 0)}
                </p>
              </div>
              <div>
                <p className="text-slate-500">Model</p>
                <p className="font-mono text-slate-200 truncate">{span.model ?? "—"}</p>
              </div>
            </div>
          </div>
        )}

        {/* Error message */}
        {span.error_message && (
          <div className="rounded-lg border border-red-500/20 bg-red-500/10 p-3">
            <div className="flex items-start gap-2">
              <AlertCircle className="h-4 w-4 text-red-400 mt-0.5 shrink-0" />
              <p className="text-xs text-red-300 font-mono break-all">
                {span.error_message}
              </p>
            </div>
          </div>
        )}

        {/* Input */}
        {span.input && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-slate-400">Input</p>
            <pre className="overflow-auto rounded-lg bg-slate-800 p-3 text-xs text-slate-300 max-h-40 whitespace-pre-wrap break-all">
              {span.input}
            </pre>
          </div>
        )}

        {/* Output */}
        {span.output && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-slate-400">Output</p>
            <pre className="overflow-auto rounded-lg bg-slate-800 p-3 text-xs text-slate-300 max-h-40 whitespace-pre-wrap break-all">
              {span.output}
            </pre>
          </div>
        )}

        {/* Attributes */}
        {Object.keys(span.attributes ?? {}).length > 0 && (
          <div>
            <p className="mb-1.5 text-xs font-medium text-slate-400">Attributes</p>
            <div className="space-y-1">
              {Object.entries(span.attributes).map(([key, value]) => (
                <div key={key} className="flex gap-2 text-xs">
                  <span className="shrink-0 font-mono text-slate-500 w-32 truncate">{key}</span>
                  <span className="font-mono text-slate-300 break-all">
                    {typeof value === "object"
                      ? JSON.stringify(value)
                      : String(value)}
                  </span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Span tree builder ─────────────────────────────────────────────────────────

interface SpanNode {
  span: Span;
  depth: number;
}

function flattenSpanTree(spans: Span[]): SpanNode[] {
  const childrenMap = new Map<string | null, Span[]>();
  for (const span of spans) {
    const parent = span.parent_span_id ?? null;
    if (!childrenMap.has(parent)) childrenMap.set(parent, []);
    childrenMap.get(parent)!.push(span);
  }

  // Sort children by start time
  for (const children of childrenMap.values()) {
    children.sort(
      (a, b) =>
        new Date(a.start_time).getTime() - new Date(b.start_time).getTime()
    );
  }

  const result: SpanNode[] = [];
  function walk(parentId: string | null, depth: number) {
    const children = childrenMap.get(parentId) ?? [];
    for (const span of children) {
      result.push({ span, depth });
      walk(span.span_id, depth + 1);
    }
  }

  walk(null, 0);
  // Fallback: include any spans not reachable from root
  const included = new Set(result.map((n) => n.span.span_id));
  for (const span of spans) {
    if (!included.has(span.span_id)) {
      result.push({ span, depth: 0 });
    }
  }

  return result;
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function TraceDetailPage({
  params,
}: {
  params: { traceId: string };
}) {
  const { trace, isLoading, isError } = useTrace(params.traceId);
  const [selectedSpan, setSelectedSpan] = useState<Span | null>(null);

  if (isError) {
    return (
      <div className="flex flex-col items-center justify-center h-64 gap-4">
        <p className="text-slate-400 text-sm">Failed to load trace</p>
        <Link href="/traces">
          <Button variant="outline" size="sm">
            <ArrowLeft className="h-4 w-4" />
            Back to traces
          </Button>
        </Link>
      </div>
    );
  }

  if (isLoading) {
    return (
      <div className="p-6 space-y-5">
        <Skeleton className="h-8 w-48" />
        <div className="grid grid-cols-4 gap-4">
          {Array.from({ length: 4 }).map((_, i) => (
            <Skeleton key={i} className="h-24 rounded-xl" />
          ))}
        </div>
        <Skeleton className="h-64 rounded-xl" />
      </div>
    );
  }

  if (!trace) return null;

  const traceStart = Math.min(
    ...trace.spans.map((s) => new Date(s.start_time).getTime())
  );
  const traceEnd = Math.max(
    ...trace.spans.map((s) => new Date(s.end_time).getTime())
  );
  const traceDuration = traceEnd - traceStart;

  const flatSpans = flattenSpanTree(trace.spans);

  return (
    <div className="p-6 space-y-5">
      {/* Back + header */}
      <div className="flex items-start gap-4">
        <Link href="/traces">
          <Button variant="ghost" size="icon" className="mt-0.5 shrink-0">
            <ArrowLeft className="h-4 w-4" />
          </Button>
        </Link>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-3">
            <h1 className="text-lg font-semibold text-slate-100">
              {trace.service_name}
            </h1>
            <Badge variant={statusVariant(trace.status)}>{trace.status}</Badge>
          </div>
          <p className="mt-1 font-mono text-xs text-slate-500 break-all">
            {trace.trace_id}
          </p>
          <p className="mt-0.5 text-xs text-slate-500">
            {formatDate(trace.created_at)}
          </p>
        </div>
      </div>

      {/* Summary cards */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Card>
          <CardContent className="pt-5">
            <div className="flex items-center gap-2 text-slate-400 mb-1.5">
              <Clock className="h-4 w-4" />
              <span className="text-xs">Duration</span>
            </div>
            <p className="text-2xl font-bold text-slate-100 font-mono">
              {formatDuration(trace.duration_ms)}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-5">
            <div className="flex items-center gap-2 text-slate-400 mb-1.5">
              <span className="text-xs">Spans</span>
            </div>
            <p className="text-2xl font-bold text-slate-100">
              {trace.spans.length}
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-5">
            <div className="flex items-center gap-2 text-slate-400 mb-1.5">
              <Coins className="h-4 w-4" />
              <span className="text-xs">Tokens</span>
            </div>
            <p className="text-2xl font-bold text-slate-100 font-mono">
              {formatTokens(
                (trace.prompt_tokens ?? 0) + (trace.completion_tokens ?? 0)
              )}
            </p>
            {(trace.prompt_tokens != null || trace.completion_tokens != null) && (
              <p className="text-xs text-slate-500 mt-0.5">
                {formatTokens(trace.prompt_tokens ?? 0)} in /{" "}
                {formatTokens(trace.completion_tokens ?? 0)} out
              </p>
            )}
          </CardContent>
        </Card>

        <Card>
          <CardContent className="pt-5">
            <div className="flex items-center gap-2 text-slate-400 mb-1.5">
              <span className="text-xs">Est. Cost</span>
            </div>
            <p className="text-2xl font-bold text-slate-100 font-mono">
              {trace.estimated_cost != null
                ? formatCost(trace.estimated_cost)
                : "—"}
            </p>
          </CardContent>
        </Card>
      </div>

      {/* Waterfall + inspector */}
      <div className={cn("flex gap-4", selectedSpan ? "items-start" : "")}>
        {/* Waterfall */}
        <div
          className={cn(
            "flex-1 min-w-0 rounded-xl border border-slate-800 bg-slate-900 overflow-hidden",
            selectedSpan && "max-w-[calc(100%-340px)]"
          )}
        >
          {/* Waterfall header */}
          <div className="flex items-center gap-3 border-b border-slate-800 px-3 py-2.5">
            <div className="w-48 text-xs font-medium text-slate-400 uppercase tracking-wider">
              Span
            </div>
            <div className="flex-1 text-xs font-medium text-slate-400 uppercase tracking-wider">
              Timeline ({formatDuration(traceDuration)})
            </div>
            <div className="w-16 text-right text-xs font-medium text-slate-400 uppercase tracking-wider">
              Duration
            </div>
          </div>

          {/* Waterfall rows */}
          <div className="p-2 space-y-0.5">
            {flatSpans.map(({ span, depth }) => (
              <WaterfallBar
                key={span.span_id}
                span={span}
                traceStart={traceStart}
                traceDuration={traceDuration}
                depth={depth}
                isSelected={selectedSpan?.span_id === span.span_id}
                onClick={() =>
                  setSelectedSpan((prev) =>
                    prev?.span_id === span.span_id ? null : span
                  )
                }
              />
            ))}
          </div>
        </div>

        {/* Inspector panel */}
        {selectedSpan && (
          <div className="w-80 flex-shrink-0 sticky top-20">
            <SpanInspector
              span={selectedSpan}
              onClose={() => setSelectedSpan(null)}
            />
          </div>
        )}
      </div>
    </div>
  );
}
