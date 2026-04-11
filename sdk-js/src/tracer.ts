import { AsyncLocalStorage } from "node:async_hooks";
import { randomUUID } from "node:crypto";
import type { Span, SpanKind, SpanStatusCode } from "./types.js";
import type { SpanQueue } from "./span-queue.js";

export interface TraceContext {
  traceId: string;
  spanId: string;
}

export interface EndSpanOptions {
  output?: unknown;
  error?: Error | string;
  tokens?: {
    prompt?: number;
    completion?: number;
  };
  attributes?: Record<string, unknown>;
}

export interface StartSpanOptions {
  kind?: SpanKind;
  attributes?: Record<string, unknown>;
  input?: unknown;
  parentSpanId?: string;
}

export class Tracer {
  private readonly queue: SpanQueue;
  private readonly serviceName: string | undefined;
  private readonly storage = new AsyncLocalStorage<TraceContext>();

  constructor(queue: SpanQueue, serviceName?: string) {
    this.queue = queue;
    this.serviceName = serviceName;
  }

  /**
   * Start a new span. If a trace context is active in the current async
   * scope the span is automatically parented.
   */
  startSpan(name: string, options: StartSpanOptions = {}): Span {
    const parent = this.storage.getStore();
    const traceId = parent?.traceId ?? randomUUID();
    const spanId = randomUUID();

    const parentSpanId = options.parentSpanId ?? parent?.spanId;

    const span: Span = {
      spanId,
      traceId,
      name,
      kind: options.kind ?? "internal",
      statusCode: "unset",
      startTime: Date.now(),
      attributes: {
        ...(this.serviceName !== undefined ? { "service.name": this.serviceName } : {}),
        ...options.attributes,
      },
      // Conditionally include optional fields to satisfy exactOptionalPropertyTypes.
      ...(parentSpanId !== undefined ? { parentSpanId } : {}),
      ...(options.input !== undefined ? { inputs: options.input } : {}),
    };

    return span;
  }

  /**
   * End a span, compute durationMs, and enqueue it for export.
   */
  endSpan(span: Span, options: EndSpanOptions = {}): Span {
    const endTime = Date.now();
    const durationMs = endTime - span.startTime;

    const resolvedErrorMessage: string | undefined =
      options.error instanceof Error
        ? options.error.message
        : typeof options.error === "string"
          ? options.error
          : span.errorMessage;

    const resolvedPromptTokens: number | undefined =
      options.tokens?.prompt ?? span.promptTokens;

    const resolvedCompletionTokens: number | undefined =
      options.tokens?.completion ?? span.completionTokens;

    const finishedSpan: Span = {
      ...span,
      endTime,
      durationMs,
      outputs: options.output ?? span.outputs,
      statusCode: (options.error ? "error" : "ok") as SpanStatusCode,
      attributes: {
        ...span.attributes,
        ...options.attributes,
      },
      // Only set optional fields when they have a value to satisfy exactOptionalPropertyTypes.
      ...(resolvedErrorMessage !== undefined ? { errorMessage: resolvedErrorMessage } : {}),
      ...(resolvedPromptTokens !== undefined ? { promptTokens: resolvedPromptTokens } : {}),
      ...(resolvedCompletionTokens !== undefined ? { completionTokens: resolvedCompletionTokens } : {}),
    };

    this.queue.enqueue(finishedSpan);
    return finishedSpan;
  }

  /**
   * Run a callback inside a new trace context. All spans started within
   * the callback are automatically associated with the same traceId.
   */
  withContext<T>(traceId: string, spanId: string, fn: () => T): T {
    return this.storage.run({ traceId, spanId }, fn);
  }

  /**
   * Returns the currently active trace context, if any.
   */
  getActiveContext(): TraceContext | undefined {
    return this.storage.getStore();
  }
}
