import useSWR from "swr";
import { api, type TracesListResponse, type TraceDetail } from "@/lib/api";

// ── Traces list ───────────────────────────────────────────────────────────────

interface UseTracesParams {
  cursor?: string;
  limit?: number;
  service_name?: string;
  status?: string;
  start_date?: string;
  end_date?: string;
}

export function useTraces(params: UseTracesParams = {}) {
  // Build a stable cache key from all params
  const key = [
    "traces",
    params.cursor ?? "",
    params.limit ?? 20,
    params.service_name ?? "",
    params.status ?? "",
    params.start_date ?? "",
    params.end_date ?? "",
  ];

  const { data, error, isLoading, mutate } = useSWR<TracesListResponse>(
    key,
    () => api.traces.list({ limit: 20, ...params }),
    {
      keepPreviousData: true,
      refreshInterval: 30_000, // refresh every 30s
    }
  );

  return {
    traces: data?.items ?? [],
    total: data?.total ?? 0,
    nextCursor: data?.next_cursor ?? null,
    prevCursor: data?.prev_cursor ?? null,
    isLoading,
    isError: !!error,
    error,
    mutate,
  };
}

// ── Single trace detail ───────────────────────────────────────────────────────

export function useTrace(traceId: string | null) {
  const { data, error, isLoading } = useSWR<TraceDetail>(
    traceId ? ["trace", traceId] : null,
    () => api.traces.get(traceId!),
    {
      revalidateOnFocus: false,
    }
  );

  return {
    trace: data ?? null,
    isLoading,
    isError: !!error,
    error,
  };
}

// ── Dashboard stats ───────────────────────────────────────────────────────────

export function useDashboardStats() {
  const { data, error, isLoading } = useSWR(
    "dashboard-stats",
    () => api.dashboard.stats(),
    { refreshInterval: 60_000 }
  );

  return { stats: data ?? null, isLoading, isError: !!error, error };
}

// ── Failures ──────────────────────────────────────────────────────────────────

interface UseFailuresParams {
  cursor?: string;
  limit?: number;
  failure_type?: string;
}

export function useFailures(params: UseFailuresParams = {}) {
  const key = [
    "failures",
    params.cursor ?? "",
    params.limit ?? 20,
    params.failure_type ?? "",
  ];

  const { data, error, isLoading, mutate } = useSWR(
    key,
    () => api.failures.list({ limit: 20, ...params }),
    { keepPreviousData: true, refreshInterval: 30_000 }
  );

  return {
    failures: data?.items ?? [],
    total: data?.total ?? 0,
    nextCursor: data?.next_cursor ?? null,
    prevCursor: data?.prev_cursor ?? null,
    isLoading,
    isError: !!error,
    error,
    mutate,
  };
}

// ── Cost breakdown ────────────────────────────────────────────────────────────

interface UseCostBreakdownParams {
  start_date?: string;
  end_date?: string;
}

export function useCostBreakdown(params: UseCostBreakdownParams = {}) {
  const key = ["cost-breakdown", params.start_date ?? "", params.end_date ?? ""];

  const { data, error, isLoading } = useSWR(
    key,
    () => api.costs.breakdown(params),
    { revalidateOnFocus: false }
  );

  return {
    breakdown: data?.items ?? [],
    totalCost: data?.total_cost ?? 0,
    dailySpend: data?.daily_spend ?? [],
    periodStart: data?.period_start ?? null,
    periodEnd: data?.period_end ?? null,
    isLoading,
    isError: !!error,
    error,
  };
}

// ── Alert rules ───────────────────────────────────────────────────────────────

export function useAlertRules() {
  const { data, error, isLoading, mutate } = useSWR(
    "alert-rules",
    () => api.alerts.list(),
    { revalidateOnFocus: false }
  );

  return {
    rules: data?.items ?? [],
    isLoading,
    isError: !!error,
    error,
    mutate,
  };
}

// ── Eval runs ─────────────────────────────────────────────────────────────────

export function useEvalRuns(limit = 50) {
  const { data, error, isLoading, mutate } = useSWR(
    ["eval-runs", limit],
    () => api.evals.list({ limit }),
    { refreshInterval: 60_000 }
  );

  return {
    runs: data?.items ?? [],
    total: data?.total ?? 0,
    isLoading,
    isError: !!error,
    error,
    mutate,
  };
}
