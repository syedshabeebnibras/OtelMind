import type { Span, IngestPayload } from "./types.js";

const DEFAULT_BATCH_SIZE = 50;
const DEFAULT_FLUSH_INTERVAL_MS = 3_000;
const MAX_RETRIES = 3;
const RETRY_BASE_DELAY_MS = 200;

export interface SpanQueueOptions {
  endpoint: string;
  apiKey: string;
  batchSize?: number;
  flushInterval?: number;
  debug?: boolean;
}

export class SpanQueue {
  private readonly endpoint: string;
  private readonly apiKey: string;
  private readonly batchSize: number;
  private readonly flushIntervalMs: number;
  private readonly debug: boolean;

  private queue: Span[] = [];
  private timer: ReturnType<typeof setInterval> | null = null;
  private flushing = false;
  private closed = false;

  constructor(options: SpanQueueOptions) {
    this.endpoint = options.endpoint;
    this.apiKey = options.apiKey;
    this.batchSize = options.batchSize ?? DEFAULT_BATCH_SIZE;
    this.flushIntervalMs = options.flushInterval ?? DEFAULT_FLUSH_INTERVAL_MS;
    this.debug = options.debug ?? false;

    this.startTimer();
  }

  enqueue(span: Span): void {
    if (this.closed) {
      this.log("warn", "SpanQueue is closed — dropping span:", span.name);
      return;
    }

    this.queue.push(span);
    this.log("debug", `Enqueued span "${span.name}" (queue size: ${this.queue.length})`);

    if (this.queue.length >= this.batchSize) {
      // Fire-and-forget; errors are logged internally.
      void this.flush();
    }
  }

  async flush(): Promise<void> {
    if (this.flushing || this.queue.length === 0) return;

    this.flushing = true;
    const batch = this.queue.splice(0, this.queue.length);

    try {
      await this.sendWithRetry(batch);
      this.log("debug", `Flushed ${batch.length} span(s) successfully`);
    } catch (err) {
      // Re-queue spans that failed after all retries so we don't silently drop.
      this.log("warn", "Failed to flush spans after retries — re-queuing:", err);
      this.queue.unshift(...batch);
    } finally {
      this.flushing = false;
    }
  }

  async shutdown(): Promise<void> {
    this.closed = true;
    this.stopTimer();
    await this.flush();
  }

  private startTimer(): void {
    this.timer = setInterval(() => {
      void this.flush();
    }, this.flushIntervalMs);

    // Allow the process to exit even if this timer is still active.
    // NodeJS.Timeout has .unref(); guard protects against non-Node runtimes.
    (this.timer as { unref?: () => void }).unref?.();
  }

  private stopTimer(): void {
    if (this.timer !== null) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }

  private async sendWithRetry(spans: Span[]): Promise<void> {
    const payload: IngestPayload = { spans };
    const url = `${this.endpoint}/ingest`;

    let lastError: unknown;

    for (let attempt = 0; attempt <= MAX_RETRIES; attempt++) {
      if (attempt > 0) {
        const delay = RETRY_BASE_DELAY_MS * 2 ** (attempt - 1);
        this.log("debug", `Retry attempt ${attempt} in ${delay}ms`);
        await sleep(delay);
      }

      try {
        const response = await fetch(url, {
          method: "POST",
          headers: {
            "Content-Type": "application/json",
            "x-api-key": this.apiKey,
          },
          body: JSON.stringify(payload),
        });

        if (response.ok) return;

        // Only retry on server errors (5xx).
        if (response.status >= 500) {
          lastError = new Error(`Server error ${response.status}: ${await response.text()}`);
          continue;
        }

        // 4xx errors are not retriable.
        throw new Error(
          `OtelMind ingest rejected (${response.status}): ${await response.text()}`
        );
      } catch (err) {
        if (isNetworkError(err)) {
          lastError = err;
          continue;
        }
        throw err;
      }
    }

    throw lastError ?? new Error("Unknown flush error");
  }

  private log(level: "debug" | "warn", ...args: unknown[]): void {
    if (!this.debug && level === "debug") return;
    const prefix = `[OtelMind SpanQueue]`;
    if (level === "warn") {
      console.warn(prefix, ...args);
    } else {
      console.debug(prefix, ...args);
    }
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function isNetworkError(err: unknown): boolean {
  return (
    err instanceof TypeError &&
    (err.message.includes("fetch") ||
      err.message.includes("network") ||
      err.message.includes("ECONNREFUSED") ||
      err.message.includes("ENOTFOUND"))
  );
}
