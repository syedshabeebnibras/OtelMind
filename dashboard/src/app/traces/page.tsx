"use client";

import { useState, useCallback } from "react";
import Link from "next/link";
import { Search, SlidersHorizontal, ChevronLeft, ChevronRight, RefreshCw } from "lucide-react";
import { useTraces } from "@/hooks/use-traces";
import { Badge, statusVariant } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { formatDate, formatDuration, truncate } from "@/lib/utils";

const STATUS_OPTIONS = [
  { value: "all", label: "All statuses" },
  { value: "success", label: "Success" },
  { value: "error", label: "Error" },
  { value: "warning", label: "Warning" },
  { value: "running", label: "Running" },
];

export default function TracesPage() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("all");
  const [startDate, setStartDate] = useState("");
  const [endDate, setEndDate] = useState("");
  const [cursor, setCursor] = useState<string | undefined>(undefined);
  const [cursorStack, setCursorStack] = useState<string[]>([]);

  const { traces, total, nextCursor, isLoading, isError, mutate } = useTraces({
    cursor,
    service_name: search || undefined,
    status: statusFilter !== "all" ? statusFilter : undefined,
    start_date: startDate || undefined,
    end_date: endDate || undefined,
  });

  const handleNext = useCallback(() => {
    if (!nextCursor) return;
    setCursorStack((prev) => [...prev, cursor ?? ""]);
    setCursor(nextCursor);
  }, [nextCursor, cursor]);

  const handlePrev = useCallback(() => {
    const stack = [...cursorStack];
    const prev = stack.pop();
    setCursorStack(stack);
    setCursor(prev || undefined);
  }, [cursorStack]);

  const handleReset = useCallback(() => {
    setSearch("");
    setStatusFilter("all");
    setStartDate("");
    setEndDate("");
    setCursor(undefined);
    setCursorStack([]);
  }, []);

  const hasFilters =
    search !== "" ||
    statusFilter !== "all" ||
    startDate !== "" ||
    endDate !== "";

  return (
    <div className="p-6 space-y-5">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-semibold text-slate-100">Traces</h1>
          <p className="mt-0.5 text-sm text-slate-400">
            {isLoading ? "Loading…" : `${total.toLocaleString()} total traces`}
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

      {/* Filter bar */}
      <div className="flex flex-wrap gap-3 rounded-xl border border-slate-800 bg-slate-900 p-4">
        <div className="relative min-w-[220px] flex-1">
          <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-500" />
          <Input
            placeholder="Filter by service name…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setCursor(undefined);
              setCursorStack([]);
            }}
            className="pl-9"
          />
        </div>

        <Select
          value={statusFilter}
          onValueChange={(v) => {
            setStatusFilter(v);
            setCursor(undefined);
            setCursorStack([]);
          }}
        >
          <SelectTrigger className="w-44">
            <SlidersHorizontal className="h-4 w-4 text-slate-500" />
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            {STATUS_OPTIONS.map((opt) => (
              <SelectItem key={opt.value} value={opt.value}>
                {opt.label}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>

        <Input
          type="date"
          value={startDate}
          onChange={(e) => {
            setStartDate(e.target.value);
            setCursor(undefined);
            setCursorStack([]);
          }}
          className="w-40 text-slate-400"
          title="Start date"
        />
        <Input
          type="date"
          value={endDate}
          onChange={(e) => {
            setEndDate(e.target.value);
            setCursor(undefined);
            setCursorStack([]);
          }}
          className="w-40 text-slate-400"
          title="End date"
        />

        {hasFilters && (
          <Button variant="ghost" size="sm" onClick={handleReset}>
            Clear filters
          </Button>
        )}
      </div>

      {/* Table */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 overflow-hidden">
        {isError ? (
          <div className="flex flex-col items-center justify-center py-16 text-slate-500">
            <p className="text-sm font-medium">Failed to load traces</p>
            <p className="mt-1 text-xs">Check that the API server is running</p>
            <Button variant="outline" size="sm" className="mt-4" onClick={() => mutate()}>
              Retry
            </Button>
          </div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[280px]">Trace ID</TableHead>
                <TableHead>Service</TableHead>
                <TableHead>Status</TableHead>
                <TableHead className="text-right">Duration</TableHead>
                <TableHead>Created At</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {isLoading
                ? Array.from({ length: 8 }).map((_, i) => (
                    <TableRow key={i}>
                      <TableCell><Skeleton className="h-4 w-48" /></TableCell>
                      <TableCell><Skeleton className="h-4 w-28" /></TableCell>
                      <TableCell><Skeleton className="h-5 w-16 rounded-full" /></TableCell>
                      <TableCell className="text-right"><Skeleton className="h-4 w-14 ml-auto" /></TableCell>
                      <TableCell><Skeleton className="h-4 w-36" /></TableCell>
                    </TableRow>
                  ))
                : traces.length === 0
                ? (
                    <TableRow>
                      <TableCell colSpan={5} className="h-32 text-center text-slate-500 text-sm">
                        No traces found
                        {hasFilters && " — try clearing your filters"}
                      </TableCell>
                    </TableRow>
                  )
                : traces.map((trace) => (
                    <TableRow key={trace.trace_id}>
                      <TableCell>
                        <Link
                          href={`/traces/${trace.trace_id}`}
                          className="font-mono text-xs text-blue-400 hover:text-blue-300 hover:underline transition-colors"
                        >
                          {truncate(trace.trace_id, 36)}
                        </Link>
                      </TableCell>
                      <TableCell>
                        <span className="text-sm text-slate-300 font-medium">
                          {trace.service_name}
                        </span>
                      </TableCell>
                      <TableCell>
                        <Badge variant={statusVariant(trace.status)}>
                          {trace.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right font-mono text-xs text-slate-400">
                        {formatDuration(trace.duration_ms)}
                      </TableCell>
                      <TableCell className="text-xs text-slate-400">
                        {formatDate(trace.created_at)}
                      </TableCell>
                    </TableRow>
                  ))}
            </TableBody>
          </Table>
        )}
      </div>

      {/* Pagination */}
      {!isError && (
        <div className="flex items-center justify-between">
          <p className="text-xs text-slate-500">
            {traces.length > 0
              ? `Showing ${traces.length} of ${total.toLocaleString()} traces`
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
