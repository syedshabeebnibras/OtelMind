import { randomUUID } from "node:crypto";
import type { OtelMindConfig, Span } from "./types.js";
import { SpanQueue } from "./span-queue.js";
import { Tracer } from "./tracer.js";
import { instrumentOpenAI } from "./instrumentation/openai.js";
import { instrumentAnthropic } from "./instrumentation/anthropic.js";

const DEFAULT_ENDPOINT = "https://ingest.otelmind.io";

// ---------------------------------------------------------------------------
// ActiveSpan — a convenience handle returned by OtelMindClient.startSpan
// ---------------------------------------------------------------------------

export class ActiveSpan {
  private readonly span: Span;
  private readonly tracer: Tracer;
  private ended = false;

  constructor(span: Span, tracer: Tracer) {
    this.span = span;
    this.tracer = tracer;
  }

  /** Finalise the span with an optional output value. */
  end(result?: unknown): void {
    if (this.ended) return;
    this.ended = true;
    this.tracer.endSpan(this.span, { output: result });
  }

  /** Mark the span as failed with the given error. */
  setError(err: Error | string): void {
    if (this.ended) return;
    this.ended = true;
    this.tracer.endSpan(this.span, {
      error: err,
    });
  }

  /** Record token usage (can be called before end()). */
  addTokens(prompt: number, completion: number): void {
    this.span.promptTokens = (this.span.promptTokens ?? 0) + prompt;
    this.span.completionTokens = (this.span.completionTokens ?? 0) + completion;
  }

  /** Set or merge additional attributes onto the span before it is ended. */
  setAttribute(key: string, value: unknown): void {
    this.span.attributes = { ...this.span.attributes, [key]: value };
  }

  /** The underlying span object (read-only snapshot). */
  get spanId(): string {
    return this.span.spanId;
  }

  get traceId(): string {
    return this.span.traceId;
  }
}

// ---------------------------------------------------------------------------
// OtelMindClient — main entry point
// ---------------------------------------------------------------------------

export class OtelMindClient {
  private readonly config: Required<OtelMindConfig>;
  private readonly queue: SpanQueue;
  private readonly tracer: Tracer;

  constructor(config: OtelMindConfig) {
    this.config = {
      endpoint: DEFAULT_ENDPOINT,
      serviceName: "",
      batchSize: 50,
      flushInterval: 3_000,
      debug: false,
      ...config,
    };

    this.queue = new SpanQueue({
      endpoint: this.config.endpoint,
      apiKey: this.config.apiKey,
      batchSize: this.config.batchSize,
      flushInterval: this.config.flushInterval,
      debug: this.config.debug,
    });

    this.tracer = new Tracer(
      this.queue,
      this.config.serviceName !== "" ? this.config.serviceName : undefined
    );
  }

  /**
   * Patch an OpenAI client instance so that all chat completion calls are
   * automatically traced. Returns the same client (mutated in-place).
   */
  instrumentOpenAI<T extends object>(client: T): T {
    return instrumentOpenAI(client as Parameters<typeof instrumentOpenAI>[0], this.tracer) as unknown as T;
  }

  /**
   * Patch an Anthropic client instance so that all message creation calls are
   * automatically traced. Returns the same client (mutated in-place).
   */
  instrumentAnthropic<T extends object>(client: T): T {
    return instrumentAnthropic(
      client as Parameters<typeof instrumentAnthropic>[0],
      this.tracer
    ) as unknown as T;
  }

  /**
   * Manually start a custom span. Call `.end()`, `.setError()`, or
   * `.addTokens()` on the returned ActiveSpan handle.
   */
  startSpan(
    name: string,
    attrs?: Record<string, unknown>
  ): ActiveSpan {
    const span = this.tracer.startSpan(name, { attributes: attrs });
    return new ActiveSpan(span, this.tracer);
  }

  /**
   * Wrap an async function in a span. The span is ended automatically when
   * the function resolves or rejects.
   */
  async trace<T>(
    name: string,
    fn: (span: ActiveSpan) => Promise<T>,
    attrs?: Record<string, unknown>
  ): Promise<T> {
    const activeSpan = this.startSpan(name, attrs);

    // Propagate trace context into the async subtree.
    return this.tracer.withContext(activeSpan.traceId, activeSpan.spanId, async () => {
      try {
        const result = await fn(activeSpan);
        activeSpan.end(result);
        return result;
      } catch (err) {
        activeSpan.setError(err instanceof Error ? err : String(err));
        throw err;
      }
    });
  }

  /**
   * Force-flush all buffered spans to the collector immediately.
   */
  async flush(): Promise<void> {
    await this.queue.flush();
  }

  /**
   * Gracefully flush and stop the background timer. Call this during
   * process shutdown to avoid losing buffered spans.
   */
  async shutdown(): Promise<void> {
    await this.queue.shutdown();
  }

  /**
   * Convenience: generate a new trace-level ID.
   */
  static newTraceId(): string {
    return randomUUID();
  }
}
