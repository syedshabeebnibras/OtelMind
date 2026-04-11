import type { Tracer } from "../tracer.js";
import type { Span } from "../types.js";

// ---------------------------------------------------------------------------
// Minimal structural types for @anthropic-ai/sdk v0.18+ surface we patch.
// ---------------------------------------------------------------------------

type MessageParam = {
  role: "user" | "assistant";
  content: string | ContentBlock[];
};

type ContentBlock = {
  type: string;
  text?: string;
};

type MessageCreateParamsNonStreaming = {
  model: string;
  messages: MessageParam[];
  max_tokens: number;
  stream?: false;
  system?: string;
  [key: string]: unknown;
};

type MessageCreateParamsStreaming = {
  model: string;
  messages: MessageParam[];
  max_tokens: number;
  stream: true;
  system?: string;
  [key: string]: unknown;
};

type MessageCreateParams =
  | MessageCreateParamsNonStreaming
  | MessageCreateParamsStreaming;

type Message = {
  id: string;
  type: "message";
  role: "assistant";
  content: ContentBlock[];
  model: string;
  stop_reason: string | null;
  stop_sequence: string | null;
  usage: {
    input_tokens: number;
    output_tokens: number;
  };
};

type MessageStreamEvent = {
  type: string;
  index?: number;
  delta?: {
    type?: string;
    text?: string;
    stop_reason?: string | null;
  };
  message?: Partial<Message>;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
  };
};

interface AsyncIterableStream<T> extends AsyncIterable<T> {
  [Symbol.asyncIterator](): AsyncIterator<T>;
}

type OriginalCreate = {
  (params: MessageCreateParamsStreaming): Promise<AsyncIterableStream<MessageStreamEvent>>;
  (params: MessageCreateParamsNonStreaming): Promise<Message>;
  (params: MessageCreateParams): Promise<Message | AsyncIterableStream<MessageStreamEvent>>;
};

interface PatchableAnthropic {
  messages: {
    create: OriginalCreate;
  };
}

// ---------------------------------------------------------------------------

export function instrumentAnthropic<T extends PatchableAnthropic>(client: T, tracer: Tracer): T {
  const original = client.messages.create.bind(client.messages) as OriginalCreate;

  async function patched(
    params: MessageCreateParams
  ): Promise<Message | AsyncIterableStream<MessageStreamEvent>> {
    const inputMessages: unknown[] = [];
    if (params.system) {
      inputMessages.push({ role: "system", content: params.system });
    }
    inputMessages.push(...params.messages);

    const span: Span = tracer.startSpan("anthropic.messages.create", {
      kind: "client",
      input: inputMessages,
      attributes: {
        "llm.vendor": "anthropic",
        "llm.model": params.model,
        "llm.request.max_tokens": params.max_tokens,
        "llm.request.type": params.stream === true ? "streaming" : "unary",
      },
    });

    if (params.stream === true) {
      let streamResult: AsyncIterableStream<MessageStreamEvent>;

      try {
        streamResult = await (original as (p: MessageCreateParamsStreaming) => Promise<AsyncIterableStream<MessageStreamEvent>>)(params as MessageCreateParamsStreaming);
      } catch (err) {
        tracer.endSpan(span, { error: err instanceof Error ? err : String(err) });
        throw err;
      }

      return wrapStream(streamResult, span, tracer);
    }

    // Non-streaming path.
    try {
      const result = await (original as (p: MessageCreateParamsNonStreaming) => Promise<Message>)(params as MessageCreateParamsNonStreaming);

      const outputText = extractTextFromContent(result.content);

      tracer.endSpan(span, {
        output: outputText,
        tokens: {
          prompt: result.usage.input_tokens,
          completion: result.usage.output_tokens,
        },
        attributes: {
          "llm.model": result.model,
          "llm.response.stop_reason": result.stop_reason ?? undefined,
        },
      });

      return result;
    } catch (err) {
      tracer.endSpan(span, { error: err instanceof Error ? err : String(err) });
      throw err;
    }
  }

  (client.messages as { create: unknown }).create = patched as unknown as OriginalCreate;

  return client;
}

function wrapStream(
  stream: AsyncIterableStream<MessageStreamEvent>,
  span: Span,
  tracer: Tracer
): AsyncIterableStream<MessageStreamEvent> {
  async function* generator(): AsyncGenerator<MessageStreamEvent> {
    const textParts: string[] = [];
    let inputTokens: number | undefined;
    let outputTokens: number | undefined;
    let stopReason: string | null | undefined;
    let model: string | undefined;

    try {
      for await (const event of stream) {
        // Accumulate text deltas.
        if (event.type === "content_block_delta" && event.delta?.type === "text_delta") {
          if (event.delta.text) textParts.push(event.delta.text);
        }

        // Capture usage from message_delta or message_start events.
        if (event.type === "message_start" && event.message) {
          model = event.message.model;
          if (event.message.usage) {
            inputTokens = event.message.usage.input_tokens ?? inputTokens;
            outputTokens = event.message.usage.output_tokens ?? outputTokens;
          }
        }

        if (event.type === "message_delta") {
          if (event.delta?.stop_reason !== undefined) {
            stopReason = event.delta.stop_reason;
          }
          if (event.usage) {
            outputTokens = event.usage.output_tokens ?? outputTokens;
          }
        }

        yield event;
      }

      // Stream complete.
      tracer.endSpan(span, {
        output: textParts.join(""),
        tokens: { prompt: inputTokens, completion: outputTokens },
        attributes: {
          ...(model !== undefined ? { "llm.model": model } : {}),
          ...(stopReason !== undefined ? { "llm.response.stop_reason": stopReason } : {}),
          "llm.request.type": "streaming",
        },
      });
    } catch (err) {
      tracer.endSpan(span, { error: err instanceof Error ? err : String(err) });
      throw err;
    }
  }

  return { [Symbol.asyncIterator]: generator };
}

function extractTextFromContent(content: ContentBlock[]): string {
  return content
    .filter((block) => block.type === "text" && typeof block.text === "string")
    .map((block) => block.text ?? "")
    .join("");
}
