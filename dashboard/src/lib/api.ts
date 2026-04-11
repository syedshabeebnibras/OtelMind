// API client for OtelMind FastAPI backend
// All requests go through Next.js rewrites: /api/v1/* → NEXT_PUBLIC_API_URL/api/v1/*

const BASE = "/api/v1";

function defaultHeaders(): Record<string, string> {
  const key =
    typeof process !== "undefined"
      ? process.env.NEXT_PUBLIC_OTELMIND_API_KEY ?? ""
      : "";
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (key) h["x-api-key"] = key;
  return h;
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    headers: {
      ...defaultHeaders(),
      ...options?.headers,
    },
    ...options,
  });

  if (!res.ok) {
    const error = await res.text().catch(() => res.statusText);
    throw new Error(`API ${res.status}: ${error}`);
  }

  return res.json() as Promise<T>;
}

// ── Types ────────────────────────────────────────────────────────────────────

export interface Trace {
  trace_id: string;
  service_name: string;
  status: "success" | "error" | "warning" | "running";
  duration_ms: number;
  created_at: string;
  span_count?: number;
  model?: string;
}

export interface Span {
  span_id: string;
  trace_id: string;
  parent_span_id: string | null;
  name: string;
  service_name: string;
  status: "success" | "error" | "warning" | "running";
  start_time: string;
  end_time: string;
  duration_ms: number;
  attributes: Record<string, unknown>;
  input?: string;
  output?: string;
  model?: string;
  prompt_tokens?: number;
  completion_tokens?: number;
  error_message?: string;
}

export interface TraceDetail extends Trace {
  spans: Span[];
  total_tokens?: number;
  prompt_tokens?: number;
  completion_tokens?: number;
  estimated_cost?: number;
}

export interface TracesListResponse {
  items: Trace[];
  total: number;
  next_cursor: string | null;
  prev_cursor: string | null;
}

export interface Failure {
  id: string;
  trace_id: string;
  failure_type: string;
  confidence: number;
  detection_method: string;
  timestamp: string;
  service_name?: string;
  error_message?: string;
}

export interface FailuresListResponse {
  items: Failure[];
  total: number;
  next_cursor: string | null;
  prev_cursor: string | null;
}

export interface DashboardStats {
  total_traces: number;
  total_failures: number;
  failure_rate: number;
  avg_duration_ms: number;
  total_cost_usd: number;
  active_services: number;
  failures_by_type: Record<string, number>;
  traces_by_status: Record<string, number>;
}

export interface CostBreakdownItem {
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number;
  trace_count: number;
}

export interface CostBreakdownResponse {
  items: CostBreakdownItem[];
  total_cost: number;
  period_start: string;
  period_end: string;
  daily_spend: { date: string; cost: number }[];
}

export interface AlertRule {
  id: string;
  failure_type: string;
  threshold: number;
  channels: string[];
  enabled: boolean;
  created_at: string;
}

export interface AlertRulesResponse {
  items: AlertRule[];
}

export interface EvalRun {
  id: string;
  name: string;
  baseline: string | null;
  candidate: string | null;
  dataset: string | null;
  status: string;
  scores: Record<string, number> | null;
  passed: boolean | null;
  regression_count: number;
  improvement_count: number;
  case_count: number;
  created_at: string;
  completed_at: string | null;
}

export interface EvalRunsResponse {
  items: EvalRun[];
  total: number;
}

// ── Traces ───────────────────────────────────────────────────────────────────

export const api = {
  traces: {
    list(params: {
      cursor?: string;
      limit?: number;
      service_name?: string;
      status?: string;
      start_date?: string;
      end_date?: string;
    }): Promise<TracesListResponse> {
      const q = new URLSearchParams();
      if (params.cursor) q.set("cursor", params.cursor);
      if (params.limit) q.set("limit", String(params.limit));
      if (params.service_name) q.set("service_name", params.service_name);
      if (params.status) q.set("status", params.status);
      if (params.start_date) q.set("start_date", params.start_date);
      if (params.end_date) q.set("end_date", params.end_date);
      const qs = q.toString();
      return request<TracesListResponse>(`/traces${qs ? `?${qs}` : ""}`);
    },

    get(traceId: string): Promise<TraceDetail> {
      return request<TraceDetail>(`/traces/${traceId}`);
    },
  },

  failures: {
    list(params: {
      cursor?: string;
      limit?: number;
      failure_type?: string;
    }): Promise<FailuresListResponse> {
      const q = new URLSearchParams();
      if (params.cursor) q.set("cursor", params.cursor);
      if (params.limit) q.set("limit", String(params.limit));
      if (params.failure_type) q.set("failure_type", params.failure_type);
      const qs = q.toString();
      return request<FailuresListResponse>(`/failures${qs ? `?${qs}` : ""}`);
    },
  },

  dashboard: {
    stats(): Promise<DashboardStats> {
      return request<DashboardStats>("/dashboard/stats");
    },
  },

  costs: {
    breakdown(params?: {
      start_date?: string;
      end_date?: string;
    }): Promise<CostBreakdownResponse> {
      const q = new URLSearchParams();
      if (params?.start_date) q.set("start_date", params.start_date);
      if (params?.end_date) q.set("end_date", params.end_date);
      const qs = q.toString();
      return request<CostBreakdownResponse>(
        `/cost/breakdown${qs ? `?${qs}` : ""}`
      );
    },
  },

  alerts: {
    list(): Promise<AlertRulesResponse> {
      return request<AlertRulesResponse>("/alerts");
    },

    create(rule: Omit<AlertRule, "id" | "created_at">): Promise<AlertRule> {
      return request<AlertRule>("/alerts", {
        method: "POST",
        body: JSON.stringify(rule),
      });
    },

    update(
      id: string,
      patch: Partial<Pick<AlertRule, "enabled" | "threshold" | "channels">>
    ): Promise<AlertRule> {
      return request<AlertRule>(`/alerts/${id}`, {
        method: "PATCH",
        body: JSON.stringify(patch),
      });
    },

    delete(id: string): Promise<void> {
      return request<void>(`/alerts/${id}`, { method: "DELETE" });
    },
  },

  evals: {
    list(params?: { limit?: number; offset?: number }): Promise<EvalRunsResponse> {
      const q = new URLSearchParams();
      if (params?.limit) q.set("limit", String(params.limit));
      if (params?.offset) q.set("offset", String(params.offset));
      const qs = q.toString();
      return request<EvalRunsResponse>(`/evals${qs ? `?${qs}` : ""}`);
    },

    get(id: string): Promise<EvalRun> {
      return request<EvalRun>(`/evals/${id}`);
    },

    create(body: {
      name: string;
      baseline?: string;
      candidate?: string;
      dataset?: string;
    }): Promise<EvalRun> {
      return request<EvalRun>("/evals", {
        method: "POST",
        body: JSON.stringify(body),
      });
    },
  },
};
