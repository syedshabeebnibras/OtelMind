export interface OtelMindConfig {
  /** Your OtelMind API key (starts with om_) */
  apiKey: string;
  /** Collector endpoint. Defaults to https://ingest.otelmind.io */
  endpoint?: string;
  /** Logical name for this service / agent. Used as a span attribute. */
  serviceName?: string;
  /** Number of spans to accumulate before flushing. Default: 50 */
  batchSize?: number;
  /** Milliseconds between automatic flushes. Default: 3000 */
  flushInterval?: number;
  /** Emit debug logs to stderr. Default: false */
  debug?: boolean;
}

export type SpanKind = "internal" | "client" | "server" | "producer" | "consumer";
export type SpanStatusCode = "unset" | "ok" | "error";

export interface Span {
  spanId: string;
  traceId: string;
  parentSpanId?: string;
  name: string;
  kind: SpanKind;
  statusCode: SpanStatusCode;
  startTime: number; // Unix epoch ms
  endTime?: number; // Unix epoch ms
  durationMs?: number;
  attributes?: Record<string, unknown>;
  /** Serialised input messages / prompt */
  inputs?: unknown;
  /** Serialised model response / output */
  outputs?: unknown;
  errorMessage?: string;
  promptTokens?: number;
  completionTokens?: number;
  model?: string;
}

export interface IngestPayload {
  spans: Span[];
}
