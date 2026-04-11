// Core client
export { OtelMindClient, ActiveSpan } from "./client.js";

// Types
export type {
  OtelMindConfig,
  Span,
  IngestPayload,
  SpanKind,
  SpanStatusCode,
} from "./types.js";

// Lower-level building blocks (useful for framework integrations)
export { Tracer } from "./tracer.js";
export type { TraceContext, EndSpanOptions, StartSpanOptions } from "./tracer.js";

export { SpanQueue } from "./span-queue.js";
export type { SpanQueueOptions } from "./span-queue.js";

// Instrumentation helpers (for manual patching in custom scenarios)
export { instrumentOpenAI } from "./instrumentation/openai.js";
export { instrumentAnthropic } from "./instrumentation/anthropic.js";
